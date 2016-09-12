"""
PC-BASIC - functions.py
BASIC functions.

(c) 2013, 2014, 2015, 2016 Rob Hagemans
This file is released under the GNU GPL version 3 or later.
"""

from functools import partial
import logging
import struct

from . import values
from . import dos
from . import error
from . import tokens as tk


class Functions(object):
    """BASIC functions."""

    def __init__(self, parser):
        """Initialise function context."""
        self.parser = parser
        self.session = parser.session
        self.values = self.session.values

    def init_functions(self):
        """Initialise functions."""
        self._with_presign = {
            tk.USR: {
                None: (1, self.session.machine.usr_, values.SNG),
                tk.C_0: (1, self.session.machine.usr_, values.SNG),
                tk.C_1: (1, self.session.machine.usr_, values.SNG),
                tk.C_2: (1, self.session.machine.usr_, values.SNG),
                tk.C_3: (1, self.session.machine.usr_, values.SNG),
                tk.C_4: (1, self.session.machine.usr_, values.SNG),
                tk.C_5: (1, self.session.machine.usr_, values.SNG),
                tk.C_6: (1, self.session.machine.usr_, values.SNG),
                tk.C_7: (1, self.session.machine.usr_, values.SNG),
                tk.C_8: (1, self.session.machine.usr_, values.SNG),
                tk.C_9: (1, self.session.machine.usr_, values.SNG),
            },
            tk.IOCTL: {
                '$': (None, self.value_ioctl, None),
            },
            tk.ENVIRON: {
                '$': (1, dos.environ_, values.STR),
            },
            tk.INPUT: {
                '$': (None, self.value_input, None),
            },
            tk.ERDEV: {
                '$': (1, self.session.devices.erdev_str_, values.STR),
                None: (1, self.session.devices.erdev_, values.INT),
            },
            tk.VARPTR: {
                '$': (None, self.value_varptr_str, None),
                None: (None, self.value_varptr, None),
            },
        }
        self._bare = {
            tk.SCREEN: (3, self.session.screen.screen_fn_, None, (values.cint_, values.cint_, values.cint_), True),
            tk.FN: (None, self.value_fn, None),
            tk.ERL: (0, self.parser.erl_, values.SNG),
            tk.ERR: (0, self.parser.err_, values.INT),
            tk.STRING: (None, self.value_string, None),
            tk.INSTR: (None, self.value_instr, None),
            tk.CSRLIN: (0, self.session.screen.csrlin_, values.INT),
            tk.POINT: (2, self.session.screen.point_, None, (values.cint_, values.cint_), True),
            tk.INKEY: (0, self.session.keyboard.get_char, values.STR),
            tk.CVI: (1, values.cvi_, None),
            tk.CVS: (1, values.cvs_, None),
            tk.CVD: (1, values.cvd_, None),
            tk.MKI: (1, values.mki_, None),
            tk.MKS: (1, values.mks_, None),
            tk.MKD: (1, values.mkd_, None),
            tk.EXTERR: (1, self.session.devices.exterr_, values.INT),
            tk.DATE: (0, self.session.clock.date_fn_, values.STR),
            tk.TIME: (0, self.session.clock.time_fn_, values.STR),
            tk.PLAY: (1, self.session.sound.play_fn_, values.INT),
            tk.TIMER: (0, self.session.clock.timer_, values.SNG),
            tk.PMAP: (2, self.session.screen.pmap_, (values.cint_, values.cint_), False),
            tk.LEFT: (2, values.left_, None, (values.pass_string, values.cint_), False),
            tk.RIGHT: (2, values.right_, None, (values.pass_string, values.cint_), False),
            tk.MID: (3, values.mid_, None, (values.pass_string, values.cint_, values.cint_), True),
            tk.SGN: (1, values.sgn_, None),
            tk.INT: (1, values.int_, None),
            tk.ABS: (1, values.abs_, None),
            tk.SQR: (1, values.sqr_, None),
            tk.RND: (None, self.value_rnd, None),
            tk.SIN: (1, values.sin_, None),
            tk.LOG: (1, values.log_, None),
            tk.EXP: (1, values.exp_, None),
            tk.COS: (1, values.cos_, None),
            tk.TAN: (1, values.tan_, None),
            tk.ATN: (1, values.atn_, None),
            tk.FRE: (1, self.session.memory.fre_, values.SNG),
            tk.INP: (1, self.session.machine.inp_, values.INT),
            tk.POS: (1, self.session.screen.pos_, values.INT),
            tk.LEN: (1, values.len_, None),
            tk.STR: (1, values.str_, None),
            tk.VAL: (1, values.val_, None),
            tk.ASC: (1, values.asc_, None),
            tk.CHR: (1, values.chr_, None),
            tk.PEEK: (1, self.session.all_memory.peek_, values.INT),
            tk.SPACE: (1, values.space_, None),
            tk.OCT: (1, values.oct_, None),
            tk.HEX: (1, values.hex_, None),
            tk.LPOS: (1, self.session.files.lpos_, values.INT),
            tk.CINT: (1, values.cint_, None),
            tk.CSNG: (1, values.csng_, None),
            tk.CDBL: (1, values.cdbl_, None),
            tk.FIX: (1, values.fix_, None),
            tk.PEN: (1, self.session.events.pen.pen_, values.INT),
            tk.STICK: (1, self.session.stick.stick_, values.INT),
            tk.STRIG: (1, self.session.stick.strig_, values.INT),
            tk.EOF: (1, self.session.files.eof_, values.INT),
            tk.LOC: (1, self.session.files.loc_, values.SNG),
            tk.LOF: (1, self.session.files.lof_, values.SNG),
        }
        self._functions = set(self._with_presign.keys() + self._bare.keys())

    def __getstate__(self):
        """Pickle."""
        pickle_dict = self.__dict__.copy()
        # functools.partial objects and functions can't be pickled
        pickle_dict['_bare'] = None
        pickle_dict['_with_presign'] = None
        return pickle_dict

    def __setstate__(self, pickle_dict):
        """Unpickle."""
        self.__dict__.update(pickle_dict)

    def __contains__(self, token):
        """Check if a token is a function token."""
        return token in self._functions


    ###########################################################
    # generalised calls

    def parse_function(self, ins, token):
        """Parse a function starting with the given token."""
        ins.read(len(token))
        if token in self._bare:
            # apply functions
            fn_record = self._bare[token]
        else:
            fndict = self._with_presign[token]
            presign = ins.skip_blank_read_if(fndict)
            try:
                fn_record = fndict[presign]
            except KeyError:
                raise error.RunError(error.STX)
        narity, fn, to_type = fn_record[:3]
        if narity == 0:
            return self.values.from_value(fn(), to_type)
        elif narity == 1 and to_type:
            return self.values.from_value(fn(self.parser.parse_bracket(ins)), to_type)
        elif narity == 1:
            return fn(self.parser.parse_bracket(ins))
        elif narity > 1:
            conv, optional = fn_record[3:]
            # these functions generate type mismatch and overflow errors *before* parsing the closing parenthesis
            # while unary functions generate it *afterwards*. this is to match GW-BASIC
            return fn(*self.parse_argument_list(ins, conv, optional))
        else:
            # special case
            return fn(ins)

    def parse_argument_list(self, ins, conversions, optional=False):
        """Parse a comma-separated list of arguments and apply type conversions."""
        # required separators
        arg = []
        seps = (('(',),) + ((',',),) * (len(conversions)-1)
        for conv, sep in zip(conversions[:-1], seps[:-1]):
            ins.require_read(sep)
            arg.append(conv(self.parser.parse_expression(ins)))
        if ins.skip_blank_read_if(seps[-1]):
            arg.append(conversions[-1](self.parser.parse_expression(ins)))
        elif not optional:
            raise error.RunError(error.STX)
        if arg:
            ins.require_read((')',))
        return arg

    ###########################################################
    # special cases

    def value_fn(self, ins):
        """FN: get value of user-defined function."""
        fnname = ins.read_name()
        # must not be empty
        error.throw_if(not fnname, error.STX)
        # append sigil, if missing
        fnname = self.session.memory.complete_name(fnname)
        return self.session.user_functions.value(fnname, self.parser, ins)

    def value_varptr(self, ins):
        """VARPTR: get memory address for variable or FCB."""
        ins.require_read(('(',))
        if ins.skip_blank() == '#':
            # params holds a number
            params = self.parser.parse_file_number(ins, opt_hash=False)
        else:
            # params holds a tuple
            params = self.parser.parse_variable(ins)
        ins.require_read((')',))
        var_ptr = self.session.memory.varptr_(params)
        return self.values.from_value(var_ptr, values.INT)

    def value_varptr_str(self, ins):
        """VARPTR$: get memory address for variable."""
        ins.require_read(('(',))
        name, indices = self.parser.parse_variable(ins)
        ins.require_read((')',))
        var_ptr_str = self.session.memory.varptr_str_(name, indices)
        return self.values.from_value(var_ptr_str, values.STR)

    def value_ioctl(self, ins):
        """IOCTL$: read device control string response; not implemented."""
        ins.require_read(('(',))
        num = self.parser.parse_file_number(ins, opt_hash=True)
        ins.require_read((')',))
        return self.session.files.ioctl_(num)

    def value_instr(self, ins):
        """INSTR: find substring in string."""
        ins.require_read(('(',))
        # followed by comma so empty will raise STX
        s = self.parser.parse_expression(ins)
        start = 1
        if isinstance(s, values.Number):
            start = values.to_int(s)
            error.range_check(1, 255, start)
            ins.require_read((',',))
            s = self.parser.parse_expression(ins)
        big = values.pass_string(s)
        ins.require_read((',',))
        s = self.parser.parse_expression(ins)
        small = values.pass_string(s)
        ins.require_read((')',))
        return values.instr_(start, big, small)

    def value_rnd(self, ins):
        """RND: get pseudorandom value."""
        if ins.skip_blank() == '(':
            return self.session.randomiser.rnd(values.csng_(self.parser.parse_bracket(ins)))
        else:
            return self.session.randomiser.rnd()

    def value_string(self, ins):
        """STRING$: repeat characters."""
        ins.require_read(('(',))
        n = values.to_int(self.parser.parse_expression(ins))
        error.range_check(0, 255, n)
        ins.require_read((',',))
        asc_value_or_char = self.parser.parse_expression(ins)
        if isinstance(asc_value_or_char, values.Integer):
            error.range_check(0, 255, asc_value_or_char.to_int())
        ins.require_read((')',))
        return values.string_(asc_value_or_char, n)

    def value_input(self, ins):
        """INPUT$: get characters from the keyboard or a file."""
        ins.require_read(('(',))
        num = values.to_int(self.parser.parse_expression(ins))
        error.range_check(1, 255, num)
        infile = self.session.devices.kybd_file
        if ins.skip_blank_read_if((',',)):
            infile = self.session.files.get(self.parser.parse_file_number(ins, opt_hash=True))
        ins.require_read((')',))
        word = infile.input_(num)
        return self.values.from_value(word, values.STR)
