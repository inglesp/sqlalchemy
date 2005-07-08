# ansisql.py
# Copyright (C) 2005 Michael Bayer mike_mp@zzzcomputing.com
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.

"""defines ANSI SQL operations."""

import sqlalchemy.schema as schema

from sqlalchemy.schema import *
import sqlalchemy.sql as sql
import sqlalchemy.engine
from sqlalchemy.sql import *
from sqlalchemy.util import *
import string
        
def engine(**params):
    return ANSISQLEngine(**params)
    
class ANSISQLEngine(sqlalchemy.engine.SQLEngine):

    def tableimpl(self, table):
        return ANSISQLTableImpl(table)

    def schemagenerator(self, proxy, **params):
        return ANSISchemaGenerator(proxy, **params)
    
    def schemadropper(self, proxy, **params):
        return ANSISchemaDropper(proxy, **params)

    def connect_args(self):
        return ([],{})
        
    def dbapi(self):
        return object()
        
    def compile(self, statement, bindparams):
        compiler = ANSICompiler(statement, bindparams)
        
        statement.accept_visitor(compiler)
        return compiler

class ANSICompiler(sql.Compiled):
    def __init__(self, parent, bindparams):
        self.binds = {}
        self.bindparams = bindparams
        self.parent = parent
        self.froms = {}
        self.wheres = {}
        self.strings = {}
        
    def get_from_text(self, obj):
        return self.froms[obj]

    def get_str(self, obj):
        return self.strings[obj]

    def get_whereclause(self, obj):
        return self.wheres.get(obj, None)
        
    def get_params(self, **params):
        d = {}
        for key, value in params.iteritems():
            try:
                b = self.binds[key]
            except KeyError:
                raise "No such bind param in statement '%s': %s" % (str(self), key)
            d[b.key] = value

        for b in self.binds.values():
            if not d.has_key(b.key):
                d[b.key] = b.value

        return d
        
    def visit_column(self, column):
        if column.table.name is None:
            self.strings[column] = column.name
        else:
            self.strings[column] = "%s.%s" % (column.table.name, column.name)

    def visit_fromclause(self, fromclause):
        self.froms[fromclause] = fromclause.from_name

    def visit_textclause(self, textclause):
        if textclause.parens and len(textclause.text):
            self.strings[textclause] = "(" + textclause.text + ")"
        else:
            self.strings[textclause] = textclause.text
       
    def visit_compound(self, compound):
        if compound.operator is None:
            sep = " "
        else:
            sep = " " + compound.operator + " "
            
        if compound.parens:
            self.strings[compound] = "(" + string.join([self.get_str(c) for c in compound.clauses], sep) + ")"
        else:
            self.strings[compound] = string.join([self.get_str(c) for c in compound.clauses], sep)

    def visit_clauselist(self, list):
        self.strings[list] = string.join([self.get_str(c) for c in list.clauses], ', ')
        
    def visit_binary(self, binary):
        
        if binary.parens:
           self.strings[binary] = "(" + self.get_str(binary.left) + " " + str(binary.operator) + " " + self.get_str(binary.right) + ")"
        else:
            self.strings[binary] = self.get_str(binary.left) + " " + str(binary.operator) + " " + self.get_str(binary.right)
        
    def visit_bindparam(self, bindparam):
        self.binds[bindparam.shortname] = bindparam
        
        count = 1
        key = bindparam.key
        
        while self.binds.setdefault(key, bindparam) is not bindparam:
            key = "%s_%d" % (bindparam.key, count)
            count += 1
            
        self.strings[bindparam] = ":" + key

    def visit_alias(self, alias):
        self.froms[alias] = self.get_from_text(alias.selectable) + " " + alias.name

    def visit_select(self, select):
        inner_columns = []

        for c in select._raw_columns:
            for co in c.columns:
                inner_columns.append(co)

        if select.use_labels:
            collist = string.join(["%s AS %s" % (c.fullname, c.label) for c in inner_columns], ', ')
        else:
            collist = string.join([c.fullname for c in inner_columns], ', ')

        text = "SELECT " + collist + " FROM "
        
        whereclause = select.whereclause
        
        froms = []
        for f in select.froms.values():

            # special thingy used by oracle to redefine a join
            w = self.get_whereclause(f)
            if w is not None:
                # TODO: move this more into the oracle module
                whereclause = sql.and_(w, whereclause)
                self.visit_compound(whereclause)
                
            t = self.get_from_text(f)
            if t is not None:
                froms.append(t)

        text += string.join(froms, ', ')                

        if whereclause is not None:
            t = self.get_str(whereclause)
            if t:
                text += " WHERE " + t

        for tup in select._clauses:
            text += " " + tup[0] + " " + self.get_str(tup[1])

        self.strings[select] = text
        self.froms[select] = "(" + text + ")"


    def visit_table(self, table):
        self.froms[table] = table.name
        
    def visit_join(self, join):
        if join.isouter:
            self.froms[join] = ("(" + self.get_from_text(join.left) + " LEFT OUTER JOIN " + self.get_from_text(join.right) + 
            " ON " + self.get_str(join.onclause) + ")")
        else:
            self.froms[join] = ("(" + self.get_from_text(join.left) + " JOIN " + self.get_from_text(join.right) + 
            " ON " + self.get_str(join.onclause) + ")")

    def visit_insert(self, insert_stmt):
        colparams = insert_stmt.get_colparams(self.bindparams)

        for c in colparams:
            b = c[1]
            self.binds[b.key] = b
            self.binds[b.shortname] = b
            
        text = ("INSERT INTO " + insert_stmt.table.name + " (" + string.join([c[0].name for c in colparams], ', ') + ")" +
         " VALUES (" + string.join([":" + c[1].key for c in colparams], ', ') + ")")
         
        self.strings[insert_stmt] = text

    def visit_update(self, update_stmt):
        colparams = update_stmt.get_colparams(self.bindparams)
        
        for c in colparams:
            b = c[1]
            self.binds[b.key] = b
            self.binds[b.shortname] = b
            
        text = "UPDATE " + update_stmt.table.name + " SET " + string.join(["%s=:%s" % (c[0].name, c[1].key) for c in colparams], ', ')
        
        if update_stmt.whereclause:
            text += " WHERE " + self.get_str(update_stmt.whereclause)
         
        self.strings[update_stmt] = text

    def visit_delete(self, delete_stmt):
        text = "DELETE FROM " + delete_stmt.table.name
        
        if delete_stmt.whereclause:
            text += " WHERE " + self.get_str(delete_stmt.whereclause)
         
        self.strings[delete_stmt] = text
        
    def __str__(self):
        return self.get_str(self.parent)


    
class ANSISQLTableImpl(sql.TableImpl):
    """Selectable implementation that gets attached to a schema.Table object."""
    
    def __init__(self, table):
        sql.TableImpl.__init__(self)
        self.table = table
        self.id = self.table.name
        
    def get_from_text(self):
        return self.table.name

class ANSISchemaGenerator(sqlalchemy.engine.SchemaIterator):

    def visit_table(self, table):
        self.append("\nCREATE TABLE " + table.name + "(")
        
        separator = "\n"
        
        for column in table.columns:
            self.append(separator)
            separator = ", \n"
            self.append("\t" + column._get_specification())
            
        self.append("\n)\n\n")
        self.execute()

    def visit_column(self, column):
        pass
    
class ANSISchemaDropper(sqlalchemy.engine.SchemaIterator):
    def visit_table(self, table):
        self.append("\nDROP TABLE " + table.name)
        self.execute()


