# concat pg_encoder.py, pg_logger.py, and generate_json_trace.py in
# front of our own code to simplify loading to pyodide.

# Online Python Tutor
# https://github.com/pgbovine/OnlinePythonTutor/
#
# Copyright (C) Philip J. Guo (philip@pgbovine.net)
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

# Thanks to John DeNero for making the encoder work on both Python 2 and 3
# (circa 2012-2013)


# Given an arbitrary piece of Python data, encode it in such a manner
# that it can be later encoded into JSON.
#   http://json.org/
#
# We use this function to encode run-time traces of data structures
# to send to the front-end.
#
# Format:
#   Primitives:
#   * None, int, long, float, str, bool - unchanged
#     (json.dumps encodes these fine verbatim, except for inf, -inf, and nan)
#
#   exceptions: float('inf')  -> ['SPECIAL_FLOAT', 'Infinity']
#               float('-inf') -> ['SPECIAL_FLOAT', '-Infinity']
#               float('nan')  -> ['SPECIAL_FLOAT', 'NaN']
#               x == int(x)   -> ['SPECIAL_FLOAT', '%.1f' % x]
#               (this way, 3.0 prints as '3.0' and not as 3, which looks like an int)
#
#   If render_heap_primitives is True, then primitive values are rendered
#   on the heap as ['HEAP_PRIMITIVE', <type name>, <value>]
#
#   (for SPECIAL_FLOAT values, <value> is a list like ['SPECIAL_FLOAT', 'Infinity'])
#
#   added on 2018-06-13:
#   ['IMPORTED_FAUX_PRIMITIVE', <label>] - renders externally imported objects
#                                          like they were primitives, to save
#                                          space and to prevent from having to
#                                          recurse into of them to see internals
#
#   Compound objects:
#   * list     - ['LIST', elt1, elt2, elt3, ..., eltN]
#   * tuple    - ['TUPLE', elt1, elt2, elt3, ..., eltN]
#   * set      - ['SET', elt1, elt2, elt3, ..., eltN]
#   * dict     - ['DICT', [key1, value1], [key2, value2], ..., [keyN, valueN]]
#   * instance - ['INSTANCE', class name, [attr1, value1], [attr2, value2], ..., [attrN, valueN]]
#   * instance with non-trivial __str__ defined - ['INSTANCE_PPRINT', class name, <__str__ value>, [attr1, value1], [attr2, value2], ..., [attrN, valueN]]
#   * class    - ['CLASS', class name, [list of superclass names], [attr1, value1], [attr2, value2], ..., [attrN, valueN]]
#   * function - ['FUNCTION', function name, parent frame ID (for nested functions),
#                 [*OPTIONAL* list of pairs of default argument names/values] ] <-- final optional element added on 2018-06-13
#   * module   - ['module', module name]
#   * other    - [<type name>, string representation of object]
#   * compound object reference - ['REF', target object's unique_id]
#
# the unique_id is derived from id(), which allows us to capture aliasing


# number of significant digits for floats
FLOAT_PRECISION = 4


from collections import defaultdict
import re, types
import sys
import math

typeRE = re.compile("<type '(.*)'>")
classRE = re.compile("<class '(.*)'>")

import inspect

# TODO: maybe use the 'six' library to smooth over Py2 and Py3 incompatibilities?
is_python3 = sys.version_info[0] == 3
assert is_python3

# avoid name errors (GROSS!)
long = int
unicode = str


def is_class(dat):
    """Return whether dat is a class."""
    return isinstance(dat, type)


def is_instance(dat):
    """Return whether dat is an instance of a class."""
    return (
        type(dat) not in PRIMITIVE_TYPES
        and isinstance(type(dat), type)
        and not isinstance(dat, type)
    )


def get_name(obj):
    """Return the name of an object."""
    return obj.__name__ if hasattr(obj, "__name__") else get_name(type(obj))


PRIMITIVE_TYPES = (int, long, float, str, unicode, bool, type(None))


def encode_primitive(dat):
    t = type(dat)
    if t is float:
        if math.isinf(dat):
            if dat > 0:
                return ["SPECIAL_FLOAT", "Infinity"]
            else:
                return ["SPECIAL_FLOAT", "-Infinity"]
        elif math.isnan(dat):
            return ["SPECIAL_FLOAT", "NaN"]
        else:
            # render floats like 3.0 as '3.0' and not as 3
            if dat == int(dat):
                return ["SPECIAL_FLOAT", "%.1f" % dat]
            else:
                return round(dat, FLOAT_PRECISION)
    else:
        # return all other primitives verbatim
        return dat


# grab a line number like ' <line 2>' or ' <line 2b>'
def create_lambda_line_number(codeobj, line_to_lambda_code):
    try:
        lambda_lineno = codeobj.co_firstlineno
        lst = line_to_lambda_code[lambda_lineno]
        ind = lst.index(codeobj)
        # add a suffix for all subsequent lambdas on a line beyond the first
        # (nix this for now because order isn't guaranteed when you have
        #  multiple lambdas on the same line)
        """
    if ind > 0:
      lineno_str = str(lambda_lineno) + chr(ord('a') + ind)
    else:
      lineno_str = str(lambda_lineno)
    """
        lineno_str = str(lambda_lineno)
        return " <line " + lineno_str + ">"
    except:
        return ""


# Note that this might BLOAT MEMORY CONSUMPTION since we're holding on
# to every reference ever created by the program without ever releasing
# anything!
class ObjectEncoder:
    def __init__(self, parent):
        self.parent = parent  # should be a PGLogger object

        # Key: canonicalized small ID
        # Value: encoded (compound) heap object
        self.encoded_heap_objects = {}

        self.render_heap_primitives = parent.render_heap_primitives

        self.id_to_small_IDs = {}
        self.cur_small_ID = 1

        # wow, creating unique identifiers for lambdas is quite annoying,
        # especially if we want to properly differentiate:
        # 1.) multiple lambdas defined on the same line, and
        # 2.) the same lambda code defined multiple times on different lines
        #
        # However, it gets confused when there are multiple identical
        # lambdas on the same line, like:
        # f(lambda x:x*x, lambda y:y*y, lambda x:x*x)

        # (assumes everything is in one file)
        # Key:   line number
        # Value: list of the code objects of lambdas defined
        #        on that line in the order they were defined
        self.line_to_lambda_code = defaultdict(list)

    def should_hide_var(self, var):
        return self.parent.should_hide_var(var)

    # searches through self.parents.types_to_inline and tries
    # to match the type returned by type(obj).__name__ and
    # also 'class' and 'instance' for classes and instances, respectively
    def should_inline_object_by_type(self, obj):
        # fast-pass optimization -- common case
        if not self.parent.types_to_inline:
            return False

        # copy-pasted from the end of self.encode()
        typ = type(obj)
        typename = typ.__name__

        # pick up built-in functions too:
        if typ in (
            types.FunctionType,
            types.MethodType,
            types.BuiltinFunctionType,
            types.BuiltinMethodType,
        ):
            typename = "function"

        if not typename:
            return False

        alt_typename = None
        if is_class(obj):
            alt_typename = "class"
        elif is_instance(obj) and typename != "function":
            # if obj is an instance of the Fooo class, then we want to match
            # on both 'instance' and 'Fooo'
            # (exception: 'function' objects are sometimes also instances,
            #  but we still want to call them 'function', so ignore them)
            typename = "instance"
            class_name = None
            if hasattr(obj, "__class__"):
                # common case ...
                class_name = get_name(obj.__class__)
            else:
                # super special case for something like
                # "from datetime import datetime_CAPI" in Python 3.2,
                # which is some weird 'PyCapsule' type ...
                # http://docs.python.org/release/3.1.5/c-api/capsule.html
                class_name = get_name(type(obj))
            alt_typename = class_name

        for re_match in self.parent.types_to_inline:
            if re_match(typename):
                return True
            if alt_typename and re_match(alt_typename):
                return True
        return False

    def get_heap(self):
        return self.encoded_heap_objects

    def reset_heap(self):
        # VERY IMPORTANT to reassign to an empty dict rather than just
        # clearing the existing dict, since get_heap() could have been
        # called earlier to return a reference to a previous heap state
        self.encoded_heap_objects = {}

    def set_function_parent_frame_ID(self, ref_obj, enclosing_frame_id):
        assert ref_obj[0] == "REF"
        func_obj = self.encoded_heap_objects[ref_obj[1]]
        assert func_obj[0] == "FUNCTION"
        func_obj[-1] = enclosing_frame_id

    # return either a primitive object or an object reference;
    # and as a side effect, update encoded_heap_objects
    def encode(self, dat, get_parent):
        """Encode a data value DAT using the GET_PARENT function for parent ids."""
        # primitive type
        if not self.render_heap_primitives and type(dat) in PRIMITIVE_TYPES:
            return encode_primitive(dat)
        # compound type - return an object reference and update encoded_heap_objects
        else:
            # IMPORTED_FAUX_PRIMITIVE feature added on 2018-06-13:
            is_externally_defined = (
                False  # is dat defined in external (i.e., non-user) code?
            )
            try:
                # some objects don't return anything for getsourcefile() but DO return
                # something legit for getmodule(). e.g., "from io import StringIO"
                # so TRY getmodule *first* and then fall back on getsourcefile
                # since getmodule seems more robust empirically ...
                gsf = inspect.getmodule(dat).__file__
                if not gsf:
                    gsf = inspect.getsourcefile(dat)

                # a hacky heuristic is that if gsf is an absolute path, then it's likely
                # to be some library function and *not* in user-defined code
                #
                # NB: don't use os.path.isabs() since it doesn't work on some
                # python installations (e.g., on my webserver) and also adds a
                # dependency on the os module. just do a simple check:
                #
                # hacky: do other checks for strings that are indicative of files
                # that load user-written code, like 'generate_json_trace.py'
                if gsf and gsf[0] == "/" and "generate_json_trace.py" not in gsf:
                    is_externally_defined = True
            except (AttributeError, TypeError):
                pass  # fail soft
            my_id = id(dat)

            # if dat is an *real* object instance (and not some special built-in one
            # like ABCMeta, or a py3 function object), then DON'T treat it as
            # externally-defined because a user might be instantiating an *instance*
            # of an imported class in their own code, so we want to show that instance
            # in da visualization - ugh #hacky
            if (
                is_instance(dat)
                and type(dat)
                not in (
                    types.FunctionType,
                    types.MethodType,
                    types.BuiltinFunctionType,
                    types.BuiltinMethodType,
                )
                and hasattr(dat, "__class__")
                and (get_name(dat.__class__) != "ABCMeta")
            ):
                is_externally_defined = False

            # if this is an externally-defined object (i.e., from an imported
            # module, don't try to recurse into it since we don't want to see
            # the internals of imported objects; just return an
            # IMPORTED_FAUX_PRIMITIVE object and continue along on our way
            if is_externally_defined:
                label = "object"
                try:
                    label = type(dat).__name__
                    if is_class(dat):
                        label = "class"
                    elif is_instance(dat):
                        label = "object"
                except:
                    pass
                return ["IMPORTED_FAUX_PRIMITIVE", "imported " + label]  # punt early!

            # next check whether it should be inlined
            if self.should_inline_object_by_type(dat):
                label = "object"
                try:
                    label = type(dat).__name__
                    if is_class(dat):
                        class_name = get_name(dat)
                        label = class_name + " class"
                    elif is_instance(dat):
                        # a lot of copy-pasta from other parts of this file:
                        # TODO: clean up
                        class_name = None
                        if hasattr(dat, "__class__"):
                            # common case ...
                            class_name = get_name(dat.__class__)
                        else:
                            # super special case for something like
                            # "from datetime import datetime_CAPI" in Python 3.2,
                            # which is some weird 'PyCapsule' type ...
                            # http://docs.python.org/release/3.1.5/c-api/capsule.html
                            class_name = get_name(type(dat))
                        if class_name:
                            label = class_name + " instance"
                        else:
                            label = "instance"
                except:
                    pass
                return ["IMPORTED_FAUX_PRIMITIVE", label + " (hidden)"]  # punt early!

            try:
                my_small_id = self.id_to_small_IDs[my_id]
            except KeyError:
                my_small_id = self.cur_small_ID
                self.id_to_small_IDs[my_id] = self.cur_small_ID
                self.cur_small_ID += 1

            del my_id  # to prevent bugs later in this function

            ret = ["REF", my_small_id]

            # punt early if you've already encoded this object
            if my_small_id in self.encoded_heap_objects:
                return ret

            # major side-effect!
            new_obj = []
            self.encoded_heap_objects[my_small_id] = new_obj

            typ = type(dat)

            if typ == list:
                new_obj.append("LIST")
                for e in dat:
                    new_obj.append(self.encode(e, get_parent))
            elif typ == tuple:
                new_obj.append("TUPLE")
                for e in dat:
                    new_obj.append(self.encode(e, get_parent))
            elif typ == set:
                new_obj.append("SET")
                for e in dat:
                    new_obj.append(self.encode(e, get_parent))
            elif typ == dict:
                new_obj.append("DICT")
                for k, v in dat.items():
                    # don't display some built-in locals ...
                    if k not in ("__module__", "__return__", "__locals__"):
                        new_obj.append(
                            [self.encode(k, get_parent), self.encode(v, get_parent)]
                        )
            elif typ in (types.FunctionType, types.MethodType):
                argspec = inspect.getfullargspec(dat)

                printed_args = [e for e in argspec.args]

                default_arg_names_and_vals = []
                if argspec.defaults:
                    num_missing_defaults = len(printed_args) - len(argspec.defaults)
                    assert num_missing_defaults >= 0
                    # tricky tricky tricky how default positional arguments work!
                    for i in range(num_missing_defaults, len(printed_args)):
                        default_arg_names_and_vals.append(
                            (
                                printed_args[i],
                                self.encode(
                                    argspec.defaults[i - num_missing_defaults],
                                    get_parent,
                                ),
                            )
                        )

                if argspec.varargs:
                    printed_args.append("*" + argspec.varargs)

                # kwonlyargs come before varkw
                if argspec.kwonlyargs:
                    printed_args.extend(argspec.kwonlyargs)
                    if argspec.kwonlydefaults:
                        # iterate in order of appearance in kwonlyargs
                        for varname in argspec.kwonlyargs:
                            if varname in argspec.kwonlydefaults:
                                val = argspec.kwonlydefaults[varname]
                                default_arg_names_and_vals.append(
                                    (varname, self.encode(val, get_parent))
                                )
                if argspec.varkw:
                    printed_args.append("**" + argspec.varkw)

                func_name = get_name(dat)

                pretty_name = func_name

                # sometimes might fail for, say, <genexpr>, so just ignore
                # failures for now ...
                try:
                    pretty_name += "(" + ", ".join(printed_args) + ")"
                except TypeError:
                    pass

                # put a line number suffix on lambdas to more uniquely identify
                # them, since they don't have names
                if func_name == "<lambda>":
                    cod = dat.__code__
                    lst = self.line_to_lambda_code[cod.co_firstlineno]
                    if cod not in lst:
                        lst.append(cod)
                    pretty_name += create_lambda_line_number(
                        cod, self.line_to_lambda_code
                    )

                encoded_val = ["FUNCTION", pretty_name, None]
                if get_parent:
                    enclosing_frame_id = get_parent(dat)
                    encoded_val[2] = enclosing_frame_id
                new_obj.extend(encoded_val)
                # OPTIONAL!!!
                if default_arg_names_and_vals:
                    new_obj.append(
                        default_arg_names_and_vals
                    )  # *append* it as a single list element

            elif typ is types.BuiltinFunctionType:
                pretty_name = get_name(dat) + "(...)"
                new_obj.extend(["FUNCTION", pretty_name, None])
            elif is_class(dat) or is_instance(dat):
                self.encode_class_or_instance(dat, new_obj)
            elif typ is types.ModuleType:
                new_obj.extend(["module", dat.__name__])
            elif typ in PRIMITIVE_TYPES:
                assert self.render_heap_primitives
                new_obj.extend(
                    ["HEAP_PRIMITIVE", type(dat).__name__, encode_primitive(dat)]
                )
            else:
                typeStr = str(typ)
                m = typeRE.match(typeStr)

                if not m:
                    m = classRE.match(typeStr)

                assert m, typ
                encoded_dat = str(dat)
                new_obj.extend([m.group(1), encoded_dat])

            return ret

    def encode_class_or_instance(self, dat, new_obj):
        """Encode dat as a class or instance."""
        if is_instance(dat):
            if hasattr(dat, "__class__"):
                # common case ...
                class_name = get_name(dat.__class__)
            else:
                # super special case for something like
                # "from datetime import datetime_CAPI" in Python 3.2,
                # which is some weird 'PyCapsule' type ...
                # http://docs.python.org/release/3.1.5/c-api/capsule.html
                class_name = get_name(type(dat))

            pprint_str = None
            # do you or any of your superclasses have a __str__ field? if so, pretty-print yourself!
            if hasattr(dat, "__str__"):
                try:
                    pprint_str = dat.__str__()

                    # sometimes you'll get 'trivial' pprint_str like: '<__main__.MyObj object at 0x10f465cd0>'
                    # or '<module 'collections' ...'
                    # IGNORE THOSE!!!
                    if (
                        pprint_str[0] == "<"
                        and pprint_str[-1] == ">"
                        and (" at " in pprint_str or pprint_str.startswith("<module"))
                    ):
                        pprint_str = None
                except:
                    pass

            # TODO: filter for trivial-looking pprint_str like those produced
            # by object.__str__
            if pprint_str:
                new_obj.extend(["INSTANCE_PPRINT", class_name, pprint_str])
            else:
                new_obj.extend(["INSTANCE", class_name])

            # don't traverse inside modules, or else risk EXPLODING the visualization
            if class_name == "module":
                return
        else:
            superclass_names = [e.__name__ for e in dat.__bases__ if e is not object]
            new_obj.extend(["CLASS", get_name(dat), superclass_names])

        # traverse inside of its __dict__ to grab attributes
        # (filter out useless-seeming ones, based on anecdotal observation):
        hidden = (
            "__doc__",
            "__module__",
            "__return__",
            "__dict__",
            "__locals__",
            "__weakref__",
            "__qualname__",
        )
        if hasattr(dat, "__dict__"):
            user_attrs = sorted([e for e in dat.__dict__ if e not in hidden])
        else:
            user_attrs = []

        for attr in user_attrs:
            if not self.should_hide_var(attr):
                new_obj.append(
                    [self.encode(attr, None), self.encode(dat.__dict__[attr], None)]
                )


# Generates a JSON trace that is compatible with the js/pytutor.ts frontend
import sys, json
import sys
import bdb  # the KEY import here!
import re
import traceback
import types

# TODO: use the 'six' package to smooth out Py2 and Py3 differences
is_python3 = sys.version_info[0] == 3
import io 


# upper-bound on the number of executed lines, in order to guard against
# infinite loops
# MAX_EXECUTED_LINES = 300
MAX_EXECUTED_LINES = 1000  # on 2016-05-01, I increased the limit from 300 to 1000 for Python due to popular user demand! and I also improved the warning message

# DEBUG = False
DEBUG = True

BREAKPOINT_STR = "#break"

# if a line starts with this string, then look for a comma-separated
# list of variables after the colon. *hide* those variables in da trace
#
# 2018-06-17:
# - now supports unix-style shell globs using the syntax in
#   https://docs.python.org/3/library/fnmatch.html so you can write things
#   like '#pythontutor_hide: _*' to hide all private instance variables
# - also now filters class and instance fields in addition to top-level vars
PYTUTOR_HIDE_STR = "#pythontutor_hide:"
# 2018-06-17: a comma-separated list of types that should be displayed *inline*
# like primitives, with their actual values HIDDEN to save space. for details
# of what types are legal to specify, see:
# pg_encoder.py:should_inline_object_by_type()
# - also accepts shell globs, just like PYTUTOR_HIDE_STR
PYTUTOR_INLINE_TYPE_STR = "#pythontutor_hide_type:"

CLASS_RE = re.compile("class\s+")


# copied-pasted from translate() in https://github.com/python/cpython/blob/2.7/Lib/fnmatch.py
def globToRegex(pat):
    """Translate a shell PATTERN to a regular expression.
    There is no way to quote meta-characters.
    """

    i, n = 0, len(pat)
    res = ""
    while i < n:
        c = pat[i]
        i = i + 1
        if c == "*":
            res = res + ".*"
        elif c == "?":
            res = res + "."
        elif c == "[":
            j = i
            if j < n and pat[j] == "!":
                j = j + 1
            if j < n and pat[j] == "]":
                j = j + 1
            while j < n and pat[j] != "]":
                j = j + 1
            if j >= n:
                res = res + "\\["
            else:
                stuff = pat[i:j].replace("\\", "\\\\")
                i = j + 1
                if stuff[0] == "!":
                    stuff = "^" + stuff[1:]
                elif stuff[0] == "^":
                    stuff = "\\" + stuff
                res = "%s[%s]" % (res, stuff)
        else:
            res = res + re.escape(c)
    return res + "\Z(?ms)"


def compileGlobMatch(pattern):
    # very important to use match and *not* search!
    return re.compile(globToRegex(pattern)).match


# test globToRegex and compileGlobMatch
"""
for e in ('_*', '__*', '__*__', '*_$'):
    stuff = compileGlobMatch(e)
    for s in ('_test', 'test_', '_test_', '__test', '__test__'):
        print(e, s, stuff(s) is not None)
"""


# From http://coreygoldberg.blogspot.com/2009/05/python-redirect-or-turn-off-stdout-and.html
class NullDevice:
    def write(self, s):
        pass


assert type(__builtins__) is types.ModuleType
BUILTIN_IMPORT = __builtins__.__import__


# Support interactive user input by:
#
# 1. running the entire program up to a call to raw_input (or input in py3),
# 2. bailing and returning a trace ending in a special 'raw_input' event,
# 3. letting the web frontend issue a prompt to the user to grab a string,
# 4. RE-RUNNING the whole program with that string added to input_string_queue,
# 5. which should bring execution to the next raw_input call (if
#    available), or to termination.
# Repeat until no more raw_input calls are encountered.
# Note that this is mad inefficient, but is simple to implement!

# VERY IMPORTANT -- set random seed to 0 to ensure deterministic execution:
import random

random.seed(0)

# queue of input strings passed from either raw_input or mouse_input
# input_string_queue = []


class RawInputException(Exception):
    pass


def raw_input_wrapper(prompt=""):
    # global input_string_queue
    print(raw_input_wrapper.input_string_queue)
    if raw_input_wrapper.input_string_queue:
        input_str = raw_input_wrapper.input_string_queue.pop(0)

        # write the prompt and user input to stdout, to emulate what happens
        # at the terminal
        sys.stdout.write(str(prompt))  # always convert prompt into a string
        sys.stdout.write(input_str + "\n")  # newline to simulate the user hitting Enter
        return input_str
    # raise RawInputException(str(prompt))  # always convert prompt into a string


class MouseInputException(Exception):
    pass


# def mouse_input_wrapper(prompt=""):
#     if input_string_queue:
#         return input_string_queue.pop(0)
#     raise MouseInputException(prompt)


IGNORE_VARS = set(
    ("__builtins__", "__name__", "__exception__", "__doc__", "__package__")
)


# at_global_scope should be true only if 'frame' represents the global scope
def get_user_globals(frame, at_global_scope=False):
    d = filter_var_dict(frame.f_globals)
    # filter out __return__ for globals only, but NOT for locals
    if "__return__" in d:
        del d["__return__"]
    return d


def get_user_locals(frame):
    ret = filter_var_dict(frame.f_locals)

    # special printing of list/set/dict comprehension objects as they are
    # being built up incrementally ...
    f_name = frame.f_code.co_name
    if hasattr(frame, "f_valuestack"):
        # for dict and set comprehensions, which have their own frames:
        if f_name.endswith("comp>"):
            for i, e in enumerate(
                [e for e in frame.f_valuestack if type(e) in (list, set, dict)]
            ):
                ret["_tmp" + str(i + 1)] = e

    return ret


def filter_var_dict(d):
    ret = {}
    for k, v in d.items():
        if k not in IGNORE_VARS:
            ret[k] = v
    return ret


# yield all function objects locally-reachable from frame,
# making sure to traverse inside all compound objects ...
def visit_all_locally_reachable_function_objs(frame):
    for k, v in get_user_locals(frame).items():
        for e in visit_function_obj(v, set()):
            if e:  # only non-null if it's a function object
                assert type(e) in (types.FunctionType, types.MethodType)
                yield e


# TODO: this might be slow if we're traversing inside lots of objects:
def visit_function_obj(v, ids_seen_set):
    v_id = id(v)

    # to prevent infinite loop
    if v_id in ids_seen_set:
        yield None
    else:
        ids_seen_set.add(v_id)

        typ = type(v)

        # simple base case
        if typ in (types.FunctionType, types.MethodType):
            yield v

        # recursive cases
        elif typ in (list, tuple, set):
            for child in v:
                for child_res in visit_function_obj(child, ids_seen_set):
                    yield child_res

        elif typ == dict or is_class(v) or is_instance(v):
            contents_dict = None

            if typ == dict:
                contents_dict = v
            # warning: some classes or instances don't have __dict__ attributes
            elif hasattr(v, "__dict__"):
                contents_dict = v.__dict__

            if contents_dict:
                for key_child, val_child in contents_dict.items():
                    for key_child_res in visit_function_obj(key_child, ids_seen_set):
                        yield key_child_res
                    for val_child_res in visit_function_obj(val_child, ids_seen_set):
                        yield val_child_res

        # degenerate base case
        yield None


class PGLogger(bdb.Bdb):
    # if custom_modules is non-empty, it should be a dict mapping module
    # names to the python source code of each module. when _runscript is
    # called, it will do "from <module> import *" for all modules in
    # custom_modules before running the user's script and then trace all
    # code within custom_modules
    #
    # if separate_stdout_by_module, then have a separate stdout stream
    # for each module rather than all stdout going to a single stream
    def __init__(
        self,
        cumulative_mode,
        heap_primitives,
        show_only_outputs,
        finalizer_func,
        disable_security_checks=False,
        allow_all_modules=False,
        custom_modules=None,
        separate_stdout_by_module=False,
        probe_exprs=None,
    ):
        bdb.Bdb.__init__(self)
        self.mainpyfile = ""
        self._wait_for_mainpyfile = 0

        if probe_exprs:
            self.probe_exprs = probe_exprs
        else:
            self.probe_exprs = None

        self.separate_stdout_by_module = separate_stdout_by_module
        self.stdout_by_module = {}  # Key: module name, Value: StringIO faux-stdout

        self.modules_to_trace = set(["__main__"])  # always trace __main__!

        # Key: module name
        # Value: module's python code as a string
        self.custom_modules = custom_modules
        if self.custom_modules:
            for module_name in self.custom_modules:
                self.modules_to_trace.add(module_name)

        self.disable_security_checks = disable_security_checks
        self.allow_all_modules = allow_all_modules
        # if we allow all modules, we shouldn't do security checks
        # either since otherwise users can't really import anything
        # because that will likely involve opening files on disk, which
        # is disallowed by security checks
        if self.allow_all_modules:
            self.disable_security_checks = True

        # if True, then displays ALL stack frames that have ever existed
        # rather than only those currently on the stack (and their
        # lexical parents)
        self.cumulative_mode = cumulative_mode

        # if True, then render certain primitive objects as heap objects
        self.render_heap_primitives = heap_primitives

        # if True, then don't render any data structures in the trace,
        # and show only outputs
        self.show_only_outputs = show_only_outputs

        # a function that takes the output trace as a parameter and
        # processes it
        self.finalizer_func = finalizer_func

        # each entry contains a dict with the information for a single
        # executed line
        self.trace = []

        # if this is true, don't put any more stuff into self.trace
        self.done = False

        # if this is non-null, don't do any more tracing until a
        # 'return' instruction with a stack gotten from
        # get_stack_code_IDs() that matches wait_for_return_stack
        self.wait_for_return_stack = None

        # http://stackoverflow.com/questions/2112396/in-python-in-google-app-engine-how-do-you-capture-output-produced-by-the-print
        self.GAE_STDOUT = sys.stdout

        # Key:   function object
        # Value: parent frame
        self.closures = {}

        # Key:   code object for a lambda
        # Value: parent frame
        self.lambda_closures = {}

        # set of function objects that were defined in the global scope
        self.globally_defined_funcs = set()

        # Key: frame object
        # Value: monotonically increasing small ID, based on call order
        self.frame_ordered_ids = {}
        self.cur_frame_id = 1

        # List of frames to KEEP AROUND after the function exits.
        # If cumulative_mode is True, then keep ALL frames in
        # zombie_frames; otherwise keep only frames where
        # nested functions were defined within them.
        self.zombie_frames = []

        # set of elements within zombie_frames that are also
        # LEXICAL PARENTS of other frames
        self.parent_frames_set = set()

        # all globals that ever appeared in the program, in the order in
        # which they appeared. note that this might be a superset of all
        # the globals that exist at any particular execution point,
        # since globals might have been deleted (using, say, 'del')
        self.all_globals_in_order = []

        # very important for this single object to persist throughout
        # execution, or else canonical small IDs won't be consistent.
        self.encoder = ObjectEncoder(self)

        self.executed_script = None  # Python script to be executed!

        # if there is at least one line that ends with BREAKPOINT_STR,
        # then activate "breakpoint mode", where execution should stop
        # ONLY at breakpoint lines.
        self.breakpoints = []

        self.vars_to_hide = set()  # a set of regex match objects
        # created by compileGlobMatch() from
        # the contents of PYTUTOR_HIDE_STR
        self.types_to_inline = (
            set()
        )  # a set of regex match objects derived from PYTUTOR_INLINE_TYPE_STR

        self.prev_lineno = -1  # keep track of previous line just executed

    def should_hide_var(self, var):
        for re_match in self.vars_to_hide:
            if re_match(var):
                return True
        return False

    def get_user_stdout(self):
        def encode_stringio(sio):
            return sio.getvalue()

        if self.separate_stdout_by_module:
            ret = {}
            for module_name in self.stdout_by_module:
                ret[module_name] = encode_stringio(self.stdout_by_module[module_name])
            return ret
        else:
            # common case - single stdout stream
            return encode_stringio(self.user_stdout)

    def get_frame_id(self, cur_frame):
        return self.frame_ordered_ids[cur_frame]

    # Returns the (lexical) parent of a function value.
    def get_parent_of_function(self, val):
        if val in self.closures:
            return self.get_frame_id(self.closures[val])
        elif val in self.lambda_closures:
            return self.get_frame_id(self.lambda_closures[val])
        else:
            return None

    # Returns the (lexical) parent frame of the function that was called
    # to create the stack frame 'frame'.
    #
    # OKAY, this is a SUPER hack, but I don't see a way around it
    # since it's impossible to tell exactly which function
    # ('closure') object was called to create 'frame'.
    #
    # The Python interpreter doesn't maintain this information,
    # so unless we hack the interpreter, we will simply have
    # to make an educated guess based on the contents of local
    # variables inherited from possible parent frame candidates.
    def get_parent_frame(self, frame):
        # print >> sys.stderr, 'get_parent_frame: frame.f_code', frame.f_code
        for func_obj, parent_frame in self.closures.items():
            # ok, there's a possible match, but let's compare the
            # local variables in parent_frame to those of frame
            # to make sure. this is a hack that happens to work because in
            # Python, each stack frame inherits ('inlines') a copy of the
            # variables from its (lexical) parent frame.
            if func_obj.__code__ == frame.f_code:
                all_matched = True
                for k in frame.f_locals:
                    # Do not try to match local names
                    if k in frame.f_code.co_varnames:
                        continue
                    if k != "__return__" and k in parent_frame.f_locals:
                        if parent_frame.f_locals[k] != frame.f_locals[k]:
                            all_matched = False
                            break

                if all_matched:
                    return parent_frame

        for lambda_code_obj, parent_frame in self.lambda_closures.items():
            if lambda_code_obj == frame.f_code:
                # TODO: should we do more verification like above?!?
                return parent_frame

        return None

    def lookup_zombie_frame_by_id(self, frame_id):
        # TODO: kinda inefficient
        for e in self.zombie_frames:
            if self.get_frame_id(e) == frame_id:
                return e
        assert False  # should never get here

    # unused ...
    # def reset(self):
    #    bdb.Bdb.reset(self)
    #    self.forget()

    def forget(self):
        self.lineno = None
        self.stack = []
        self.curindex = 0
        self.curframe = None

    def setup(self, f, t):
        self.forget()
        self.stack, self.curindex = self.get_stack(f, t)
        self.curframe = self.stack[self.curindex][0]

    # should be a reasonably unique ID to match calls and returns:
    def get_stack_code_IDs(self):
        return [id(e[0].f_code) for e in self.stack]

    # Override Bdb methods

    def user_call(self, frame, argument_list):
        """This method is called when there is the remote possibility
        that we ever need to stop in this function."""
        # TODO: figure out a way to move this down to 'def interaction'
        # or right before self.trace.append ...
        if self.done:
            return

        if self._wait_for_mainpyfile:
            return
        if self.stop_here(frame):
            # delete __return__ so that on subsequent calls to
            # a generator function, the OLD yielded (returned)
            # value gets deleted from the frame ...
            try:
                del frame.f_locals["__return__"]
            except KeyError:
                pass

            self.interaction(frame, None, "call")

    def user_line(self, frame):
        """This function is called when we stop or break at this line."""
        if self.done:
            return

        if self._wait_for_mainpyfile:
            if (
                frame.f_globals["__name__"] not in self.modules_to_trace
            ) or frame.f_lineno <= 0:
                # older code:
                # if (self.canonic(frame.f_code.co_filename) != "<string>" or
                #    frame.f_lineno <= 0):
                return
            self._wait_for_mainpyfile = 0
        self.interaction(frame, None, "step_line")

    def user_return(self, frame, return_value):
        """This function is called when a return trap is set here."""
        if self.done:
            return

        frame.f_locals["__return__"] = return_value
        self.interaction(frame, None, "return")

    def user_exception(self, frame, exc_info):
        """This function is called if an exception occurs,
        but only if we are to stop at or just below this level."""
        if self.done:
            return

        exc_type, exc_value, exc_traceback = exc_info
        frame.f_locals["__exception__"] = exc_type, exc_value
        if type(exc_type) == type(""):
            exc_type_name = exc_type
        else:
            exc_type_name = exc_type.__name__

        if exc_type_name == "RawInputException":
            raw_input_arg = str(
                exc_value.args[0]
            )  # make sure it's a string so it's JSON serializable!
            self.trace.append(dict(event="raw_input", prompt=raw_input_arg))
            self.done = True
        elif exc_type_name == "MouseInputException":
            mouse_input_arg = str(
                exc_value.args[0]
            )  # make sure it's a string so it's JSON serializable!
            self.trace.append(dict(event="mouse_input", prompt=mouse_input_arg))
            self.done = True
        else:
            self.interaction(frame, exc_traceback, "exception")

    def get_script_line(self, n):
        return self.executed_script_lines[n - 1]

    # General interaction function

    def interaction(self, frame, traceback, event_type):
        self.setup(frame, traceback)
        tos = self.stack[self.curindex]
        top_frame = tos[0]
        lineno = tos[1]

        topframe_module = top_frame.f_globals["__name__"]

        # debug ...
        """
        print >> sys.stderr
        print >> sys.stderr, '=== STACK ===', 'curindex:', self.curindex
        for (e,ln) in self.stack:
          print >> sys.stderr, e.f_code.co_name + ' ' + e.f_code.co_filename + ' ' + str(ln)
        print >> sys.stderr, "top_frame", top_frame.f_code.co_name, top_frame.f_code
        """

        # don't trace inside of ANY functions that aren't user-written code
        # (e.g., those from imported modules -- e.g., random, re -- or the
        # __restricted_import__ function in this file)
        #
        # empirically, it seems like the FIRST entry in self.stack is
        # the 'run' function from bdb.py, but everything else on the
        # stack is the user program's "real stack"

        # Look only at the "topmost" frame on the stack ...

        # if we're not in a module that we are explicitly tracing, skip:
        # (this comes up in tests/backend-tests/namedtuple.txt)
        if topframe_module not in self.modules_to_trace:
            return
        # also don't trace inside of the magic "constructor" code
        if top_frame.f_code.co_name == "__new__":
            return
        # or __repr__, which is often called when running print statements
        if top_frame.f_code.co_name == "__repr__":
            return

        # don't trace if wait_for_return_stack is non-null ...
        if self.wait_for_return_stack:
            if event_type == "return" and (
                self.wait_for_return_stack == self.get_stack_code_IDs()
            ):
                self.wait_for_return_stack = None  # reset!
            return  # always bail!
        else:
            # Skip all "calls" that are actually class definitions, since
            # those faux calls produce lots of ugly cruft in the trace.
            #
            # NB: Only trigger on calls to functions defined in
            # user-written code (i.e., co_filename == '<string>'), but that
            # should already be ensured by the above check for whether we're
            # in user-written code.
            if event_type == "call":
                first_lineno = top_frame.f_code.co_firstlineno
                if topframe_module == "__main__":
                    func_line = self.get_script_line(first_lineno)
                elif topframe_module in self.custom_modules:
                    module_code = self.custom_modules[topframe_module]
                    module_code_lines = (
                        module_code.splitlines()
                    )  # TODO: maybe pre-split lines?
                    func_line = module_code_lines[first_lineno - 1]
                else:
                    # you're hosed
                    func_line = ""
                # print >> sys.stderr, func_line

                if CLASS_RE.match(func_line.lstrip()):  # ignore leading spaces
                    self.wait_for_return_stack = self.get_stack_code_IDs()
                    return

        self.encoder.reset_heap()  # VERY VERY VERY IMPORTANT,
        # or else we won't properly capture heap object mutations in the trace!

        if event_type == "call":
            # Don't be so strict about this assertion because it FAILS
            # when you're calling a generator (not for the first time),
            # since that frame has already previously been on the stack ...
            # assert top_frame not in self.frame_ordered_ids

            self.frame_ordered_ids[top_frame] = self.cur_frame_id
            self.cur_frame_id += 1

            if self.cumulative_mode:
                self.zombie_frames.append(top_frame)

        # kinda tricky to get the timing right -- basically, as soon as you
        # make a call, set sys.stdout to the stream for the appropriate
        # module, and as soon as you return, set sys.stdout to the
        # stream for your caller's module. we need to do this on the
        # return call since we want to immediately start picking up
        # prints to stdout *right after* this function returns
        if self.separate_stdout_by_module:
            if event_type == "call":
                if topframe_module in self.stdout_by_module:
                    sys.stdout = self.stdout_by_module[topframe_module]
                else:
                    sys.stdout = self.stdout_by_module["<other>"]
            elif event_type == "return" and self.curindex > 0:
                prev_tos = self.stack[self.curindex - 1]
                prev_topframe = prev_tos[0]
                prev_topframe_module = prev_topframe.f_globals["__name__"]
                if prev_topframe_module in self.stdout_by_module:
                    sys.stdout = self.stdout_by_module[prev_topframe_module]
                else:
                    sys.stdout = self.stdout_by_module["<other>"]

        # only render zombie frames that are NO LONGER on the stack
        #
        # subtle: self.stack[:self.curindex+1] is the real stack, since
        # everything after self.curindex+1 is beyond the top of the
        # stack. this seems to be relevant only when there's an exception,
        # since the ENTIRE stack is preserved but self.curindex
        # starts decrementing as the exception bubbles up the stack.
        cur_stack_frames = [e[0] for e in self.stack[: self.curindex + 1]]
        zombie_frames_to_render = [
            e for e in self.zombie_frames if e not in cur_stack_frames
        ]

        # each element is a pair of (function name, ENCODED locals dict)
        encoded_stack_locals = []

        # returns a dict with keys: function name, frame id, id of parent frame, encoded_locals dict
        def create_encoded_stack_entry(cur_frame):
            # print >> sys.stderr, '- create_encoded_stack_entry', cur_frame, self.closures, self.lambda_closures
            ret = {}

            parent_frame_id_list = []

            f = cur_frame
            while True:
                p = self.get_parent_frame(f)
                if p:
                    pid = self.get_frame_id(p)
                    assert pid
                    parent_frame_id_list.append(pid)
                    f = p
                else:
                    break

            cur_name = cur_frame.f_code.co_name

            if cur_name == "":
                cur_name = "unnamed function"

            # augment lambdas with line number
            if cur_name == "<lambda>":
                cur_name += create_lambda_line_number(
                    cur_frame.f_code, self.encoder.line_to_lambda_code
                )

            # encode in a JSON-friendly format now, in order to prevent ill
            # effects of aliasing later down the line ...
            encoded_locals = {}

            for k, v in get_user_locals(cur_frame).items():
                is_in_parent_frame = False

                # don't display locals that appear in your parents' stack frames,
                # since that's redundant
                for pid in parent_frame_id_list:
                    parent_frame = self.lookup_zombie_frame_by_id(pid)
                    if k in parent_frame.f_locals:
                        # ignore __return__, which is never copied
                        if k != "__return__":
                            # these values SHOULD BE ALIASES
                            # (don't do an 'is' check since it might not fire for primitives)
                            if parent_frame.f_locals[k] == v:
                                is_in_parent_frame = True

                if is_in_parent_frame and k not in cur_frame.f_code.co_varnames:
                    continue

                # don't display some built-in locals ...
                if k == "__module__":
                    continue

                if self.should_hide_var(k):
                    continue

                encoded_val = self.encoder.encode(v, self.get_parent_of_function)
                encoded_locals[k] = encoded_val

            # order the variable names in a sensible way:

            # Let's start with co_varnames, since it (often) contains all
            # variables in this frame, some of which might not exist yet.
            ordered_varnames = []
            for e in cur_frame.f_code.co_varnames:
                if e in encoded_locals:
                    ordered_varnames.append(e)

            # sometimes co_varnames doesn't contain all of the true local
            # variables: e.g., when executing a 'class' definition.  in that
            # case, iterate over encoded_locals and push them onto the end
            # of ordered_varnames in alphabetical order
            for e in sorted(encoded_locals.keys()):
                if e != "__return__" and e not in ordered_varnames:
                    ordered_varnames.append(e)

            # finally, put __return__ at the very end
            if "__return__" in encoded_locals:
                ordered_varnames.append("__return__")

            # doctor Python 3 initializer to look like a normal function (denero)
            if "__locals__" in encoded_locals:
                ordered_varnames.remove("__locals__")
                local = encoded_locals.pop("__locals__")
                if encoded_locals.get("__return__", True) is None:
                    encoded_locals["__return__"] = local

            # crucial sanity checks!
            assert len(ordered_varnames) == len(encoded_locals)
            for e in ordered_varnames:
                assert e in encoded_locals

            return dict(
                func_name=cur_name,
                is_parent=(cur_frame in self.parent_frames_set),
                frame_id=self.get_frame_id(cur_frame),
                parent_frame_id_list=parent_frame_id_list,
                encoded_locals=encoded_locals,
                ordered_varnames=ordered_varnames,
            )

        i = self.curindex

        # look for whether a nested function has been defined during
        # this particular call:
        if i > 1:  # i == 1 implies that there's only a global scope visible
            for v in visit_all_locally_reachable_function_objs(top_frame):
                if v not in self.closures and v not in self.globally_defined_funcs:
                    # Look for the presence of the code object (v.func_code
                    # for Python 2 or v.__code__ for Python 3) in the
                    # constant pool (f_code.co_consts) of an enclosing
                    # stack frame, and set that frame as your parent.
                    #
                    # This technique properly handles lambdas passed as
                    # function parameters. e.g., this example:
                    #
                    # def foo(x):
                    #   bar(lambda y: x + y)
                    # def bar(a):
                    #   print a(20)
                    # foo(10)
                    chosen_parent_frame = None
                    # SUPER hacky but seems to work -- use reversed(self.stack)
                    # because we want to traverse starting from the TOP of the stack
                    # (most recent frame) and find the first frame containing
                    # a constant code object that matches v.__code__ or v.func_code
                    #
                    # required for this example from Berkeley CS61a:
                    #
                    # def f(p, k):
                    #     def g():
                    #         print(k)
                    #     if k == 0:
                    #         f(g, 1)
                    # f(None, 0)
                    #
                    # there are two calls to f, each of which defines a
                    # closure g that should point to the respective frame.
                    #
                    # note that for the second call to f, the parent of the
                    # g defined in there should be that frame, which is at
                    # the TOP of the stack. this reversed() hack does the
                    # right thing. note that if you don't traverse the stack
                    # backwards, then you will mistakenly get the parent as
                    # the FIRST f frame (bottom of the stack).
                    for my_frame, my_lineno in reversed(self.stack):
                        if chosen_parent_frame:
                            break

                        for frame_const in my_frame.f_code.co_consts:
                            if frame_const is v.__code__:
                                chosen_parent_frame = my_frame
                                break

                    # 2013-12-01 commented out this line so tests/backend-tests/papajohn-monster.txt
                    # works without an assertion failure ...
                    # assert chosen_parent_frame # I hope this always passes :0

                    # this condition should be False for functions declared in global scope ...
                    if chosen_parent_frame in self.frame_ordered_ids:
                        self.closures[v] = chosen_parent_frame
                        self.parent_frames_set.add(
                            chosen_parent_frame
                        )  # unequivocally add to this set!!!
                        if not chosen_parent_frame in self.zombie_frames:
                            self.zombie_frames.append(chosen_parent_frame)
            else:
                # look for code objects of lambdas defined within this
                # function, which comes up in cases like line 2 of:
                # def x(y):
                #   (lambda z: lambda w: z+y)(y)
                #
                # x(42)
                if top_frame.f_code.co_consts:
                    for e in top_frame.f_code.co_consts:
                        if type(e) == types.CodeType and e.co_name == "<lambda>":
                            # TODO: what if it's already in lambda_closures?
                            self.lambda_closures[e] = top_frame
                            self.parent_frames_set.add(
                                top_frame
                            )  # copy-paste from above
                            if not top_frame in self.zombie_frames:
                                self.zombie_frames.append(top_frame)
        else:
            # if there is only a global scope visible ...
            for k, v in get_user_globals(top_frame).items():
                if (
                    type(v) in (types.FunctionType, types.MethodType)
                    and v not in self.closures
                ):
                    self.globally_defined_funcs.add(v)

        # climb up until you find '<module>', which is (hopefully) the global scope
        top_frame = None
        while True:
            cur_frame = self.stack[i][0]
            cur_name = cur_frame.f_code.co_name
            if cur_name == "<module>":
                break

            # do this check because in some cases, certain frames on the
            # stack might NOT be tracked, so don't push a stack entry for
            # those frames. this happens when you have a callback function
            # in an imported module. e.g., your code:
            #     def foo():
            #         bar(baz)
            #
            #     def baz(): pass
            #
            # imported module code:
            #     def bar(callback_func):
            #         callback_func()
            #
            # when baz is executing, the real stack is [foo, bar, baz] but
            # bar is in imported module code, so pg_logger doesn't trace
            # it, and it doesn't show up in frame_ordered_ids. thus, the
            # stack to render should only be [foo, baz].
            if cur_frame in self.frame_ordered_ids:
                encoded_stack_locals.append(create_encoded_stack_entry(cur_frame))
                if not top_frame:
                    top_frame = cur_frame
            i -= 1

        zombie_encoded_stack_locals = [
            create_encoded_stack_entry(e) for e in zombie_frames_to_render
        ]

        # encode in a JSON-friendly format now, in order to prevent ill
        # effects of aliasing later down the line ...
        encoded_globals = {}
        cur_globals_dict = get_user_globals(
            tos[0], at_global_scope=(self.curindex <= 1)
        )
        for k, v in cur_globals_dict.items():
            if self.should_hide_var(k):
                continue

            encoded_val = self.encoder.encode(v, self.get_parent_of_function)
            encoded_globals[k] = encoded_val

            if k not in self.all_globals_in_order:
                self.all_globals_in_order.append(k)

        # filter out globals that don't exist at this execution point
        # (because they've been, say, deleted with 'del')
        ordered_globals = [e for e in self.all_globals_in_order if e in encoded_globals]
        assert len(ordered_globals) == len(encoded_globals)

        # merge zombie_encoded_stack_locals and encoded_stack_locals
        # into one master ordered list using some simple rules for
        # making it look aesthetically pretty
        stack_to_render = []

        # first push all regular stack entries
        if encoded_stack_locals:
            for e in encoded_stack_locals:
                e["is_zombie"] = False
                e["is_highlighted"] = False
                stack_to_render.append(e)

            # highlight the top-most active stack entry
            stack_to_render[0]["is_highlighted"] = True

        # now push all zombie stack entries
        for e in zombie_encoded_stack_locals:
            # don't display return value for zombie frames
            # TODO: reconsider ...
            """
            try:
              e['ordered_varnames'].remove('__return__')
            except ValueError:
              pass
            """

            e["is_zombie"] = True
            e["is_highlighted"] = False  # never highlight zombie entries

            stack_to_render.append(e)

        # now sort by frame_id since that sorts frames in "chronological
        # order" based on the order they were invoked
        stack_to_render.sort(key=lambda e: e["frame_id"])

        # create a unique hash for this stack entry, so that the
        # frontend can uniquely identify it when doing incremental
        # rendering. the strategy is to use a frankenstein-like mix of the
        # relevant fields to properly disambiguate closures and recursive
        # calls to the same function
        for e in stack_to_render:
            hash_str = e["func_name"]
            # frame_id is UNIQUE, so it can disambiguate recursive calls
            hash_str += "_f" + str(e["frame_id"])

            # needed to refresh GUI display ...
            if e["is_parent"]:
                hash_str += "_p"

            # TODO: this is no longer needed, right? (since frame_id is unique)
            # if e['parent_frame_id_list']:
            #  hash_str += '_p' + '_'.join([str(i) for i in e['parent_frame_id_list']])
            if e["is_zombie"]:
                hash_str += "_z"

            e["unique_hash"] = hash_str

        # handle probe_exprs *before* encoding the heap with self.encoder.get_heap
        encoded_probe_vals = {}
        if self.probe_exprs:
            if top_frame:  # are we in a function call?
                top_frame_locals = get_user_locals(top_frame)
            else:
                top_frame_locals = {}
            for e in self.probe_exprs:
                try:
                    # evaluate it with globals + locals of the top frame ...
                    probe_val = eval(e, cur_globals_dict, top_frame_locals)
                    encoded_probe_vals[e] = self.encoder.encode(
                        probe_val, self.get_parent_of_function
                    )
                except:
                    pass  # don't encode the value if there's been an error

        if self.show_only_outputs:
            trace_entry = dict(
                line=lineno,
                event=event_type,
                func_name=tos[0].f_code.co_name,
                globals={},
                ordered_globals=[],
                stack_to_render=[],
                heap={},
                stdout=self.get_user_stdout(),
            )
        else:
            trace_entry = dict(
                line=lineno,
                event=event_type,
                func_name=tos[0].f_code.co_name,
                globals=encoded_globals,
                ordered_globals=ordered_globals,
                stack_to_render=stack_to_render,
                heap=self.encoder.get_heap(),
                stdout=self.get_user_stdout(),
            )
            if encoded_probe_vals:
                trace_entry["probe_exprs"] = encoded_probe_vals

        # set a 'custom_module_name' field if we're executing in a module
        # that's not the __main__ script:
        if topframe_module != "__main__":
            trace_entry["custom_module_name"] = topframe_module

        # if there's an exception, then record its info:
        if event_type == "exception":
            # always check in f_locals
            exc = frame.f_locals["__exception__"]
            trace_entry["exception_msg"] = exc[0].__name__ + ": " + str(exc[1])

        # append to the trace only the breakpoint line and the next
        # executed line, so that if you set only ONE breakpoint, OPT shows
        # the state before and after that line gets executed.
        append_to_trace = True
        if self.breakpoints:
            if not (
                (lineno in self.breakpoints) or (self.prev_lineno in self.breakpoints)
            ):
                append_to_trace = False

            # TRICKY -- however, if there's an exception, then ALWAYS
            # append it to the trace, so that the error can be displayed
            if event_type == "exception":
                append_to_trace = True

        self.prev_lineno = lineno

        if append_to_trace:
            self.trace.append(trace_entry)

        # sanity check to make sure the state of the world at a 'call' instruction
        # is identical to that at the instruction immediately following it ...
        """
        if len(self.trace) > 1:
          cur = self.trace[-1]
          prev = self.trace[-2]
          if prev['event'] == 'call':
            assert cur['globals'] == prev['globals']
            for (s1, s2) in zip(cur['stack_to_render'], prev['stack_to_render']):
              assert s1 == s2
            assert cur['heap'] == prev['heap']
            assert cur['stdout'] == prev['stdout']
        """

        if len(self.trace) >= MAX_EXECUTED_LINES:
            self.trace.append(
                dict(
                    event="instruction_limit_reached",
                    exception_msg="Stopped after running "
                    + str(MAX_EXECUTED_LINES)
                    + " steps. Please shorten your code,\nsince Python Tutor is not designed to handle long-running code.",
                )
            )
            self.force_terminate()

        self.forget()

    def _runscript(self, script_str):
        self.executed_script = script_str
        self.executed_script_lines = self.executed_script.splitlines()

        for i, line in enumerate(self.executed_script_lines):
            line_no = i + 1
            # subtle -- if the stripped line starts with '#break', that
            # means it may be a commented-out version of a normal Python
            # 'break' statement, which shouldn't be confused with an
            # OPT user-defined breakpoint!
            #
            # TODO: this still fails when someone writes something like
            # '##break' since it doesn't start with '#break'!!! i just
            # picked an unfortunate name that's also a python keyword :0
            if line.endswith(BREAKPOINT_STR) and not line.strip().startswith(
                BREAKPOINT_STR
            ):
                self.breakpoints.append(line_no)

            if line.startswith(PYTUTOR_HIDE_STR):
                hide_vars = line[len(PYTUTOR_HIDE_STR) :]
                # remember to call strip() -> compileGlobMatch()
                hide_vars = [compileGlobMatch(e.strip()) for e in hide_vars.split(",")]
                self.vars_to_hide.update(hide_vars)

            if line.startswith(PYTUTOR_INLINE_TYPE_STR):
                listed_types = line[len(PYTUTOR_INLINE_TYPE_STR) :]
                # remember to call strip() -> compileGlobMatch()
                listed_types = [
                    compileGlobMatch(e.strip()) for e in listed_types.split(",")
                ]
                self.types_to_inline.update(listed_types)

        # When bdb sets tracing, a number of call and line events happens
        # BEFORE debugger even reaches user's code (and the exact sequence of
        # events depends on python version). So we take special measures to
        # avoid stopping before we reach the main script (see user_line and
        # user_call for details).
        self._wait_for_mainpyfile = 1

        # ok, let's try to sorta 'sandbox' the user script by not
        # allowing certain potentially dangerous operations.
        user_builtins = {}

        assert type(__builtins__) is types.ModuleType
        builtin_items = []
        for k in dir(__builtins__):
            builtin_items.append((k, getattr(__builtins__, k)))

        for k, v in builtin_items:
            # if k == "input":
            #     user_builtins[k] = raw_input_wrapper
            # else:
            user_builtins[k] = v

        # user_builtins["mouse_input"] = mouse_input_wrapper

        if self.separate_stdout_by_module:
            self.stdout_by_module["__main__"] = io.StringIO()
            if self.custom_modules:
                for module_name in self.custom_modules:
                    self.stdout_by_module[module_name] = io.StringIO()
            self.stdout_by_module[
                "<other>"
            ] = io.StringIO()  # catch-all for all other modules we're NOT tracing
            sys.stdout = self.stdout_by_module["<other>"]  # start with <other>
        else:
            # default -- a single unified stdout stream
            self.user_stdout = io.StringIO()
            sys.stdout = self.user_stdout

        self.ORIGINAL_STDERR = sys.stderr

        # don't do this, or else certain kinds of errors, such as syntax
        # errors, will be silently ignored. WEIRD!
        # sys.stderr = NullDevice # silence errors

        user_globals = {}

        # if there are custom_modules, 'import' them into user_globals,
        # which emulates "from <module> import *"

        if self.custom_modules:
            for mn in self.custom_modules:
                # http://code.activestate.com/recipes/82234-importing-a-dynamically-generated-module/
                new_m = types.ModuleType(mn)
                exec(self.custom_modules[mn], new_m.__dict__)  # exec in custom globals
                user_globals.update(new_m.__dict__)

        # important: do this LAST to get precedence over values in custom_modules
        user_globals.update({"__name__": "__main__", "__builtins__": user_builtins})

        try:
            # if allow_all_modules is on, then try to parse script_str into an
            # AST, traverse the tree to find all modules that it imports, and then
            # try to PRE-IMPORT all of those. if we *don't* pre-import a module,
            # then when it's imported in the user's code, it may take *forever*
            # because the bdb debugger tries to single-step thru that code
            # (i think!). run 'import pandas' to quickly test this.
            if self.allow_all_modules:
                import ast

                try:
                    all_modules_to_preimport = []
                    tree = ast.parse(script_str)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for n in node.names:
                                all_modules_to_preimport.append(n.name)
                        elif isinstance(node, ast.ImportFrom):
                            all_modules_to_preimport(node.module)

                    for m in all_modules_to_preimport:
                        if (
                            m in script_str
                        ):  # optimization: load only modules that appear in script_str
                            try:
                                __import__(m)
                            except ImportError:
                                pass
                except:
                    pass

            self.run(script_str, user_globals, user_globals)
        # sys.exit ...
        except SystemExit:
            # sys.exit(0)
            raise bdb.BdbQuit
        except:
            if DEBUG:
                traceback.print_exc()

            trace_entry = dict(event="uncaught_exception")

            (exc_type, exc_val, exc_tb) = sys.exc_info()
            if hasattr(exc_val, "lineno"):
                trace_entry["line"] = exc_val.lineno
            if hasattr(exc_val, "offset"):
                trace_entry["offset"] = exc_val.offset

            trace_entry["exception_msg"] = type(exc_val).__name__ + ": " + str(exc_val)

            # SUPER SUBTLE! if ANY exception has already been recorded by
            # the program, then DON'T record it again as an uncaught_exception.
            # This looks kinda weird since the exact exception message doesn't
            # need to match up, but in practice, there should be at most only
            # ONE exception per trace.
            already_caught = False
            for e in self.trace:
                if e["event"] == "exception":
                    already_caught = True
                    break

            if not already_caught:
                if not self.done:
                    self.trace.append(trace_entry)

            raise bdb.BdbQuit  # need to forceably STOP execution

    def force_terminate(self):
        # self.finalize()
        raise bdb.BdbQuit  # need to forceably STOP execution

    def finalize(self):
        sys.stdout = self.GAE_STDOUT  # very important!
        sys.stderr = self.ORIGINAL_STDERR

        assert len(self.trace) <= (MAX_EXECUTED_LINES + 1)

        # don't do this anymore ...
        """
      # filter all entries after 'return' from '<module>', since they
      # seem extraneous:
      res = []
      for e in self.trace:
        res.append(e)
        if e['event'] == 'return' and e['func_name'] == '<module>':
          break
      """

        res = self.trace

        # if the SECOND to last entry is an 'exception'
        # and the last entry is return from <module>, then axe the last
        # entry, for aesthetic reasons :)
        if (
            len(res) >= 2
            and res[-2]["event"] == "exception"
            and res[-1]["event"] == "return"
            and res[-1]["func_name"] == "<module>"
        ):
            res.pop()

        self.trace = res

        if self.custom_modules:
            # when there's custom_modules, call with a dict as the first parameter
            return self.finalizer_func(
                dict(
                    main_code=self.executed_script, custom_modules=self.custom_modules
                ),
                self.trace,
            )
        else:
            # common case
            return self.finalizer_func(self.executed_script, self.trace)


import json


# the MAIN meaty function!!!
def exec_script_str(script_str, raw_input_lst_json, options_json, finalizer_func):
    if options_json:
        options = json.loads(options_json)
    else:
        # defaults
        options = {
            "cumulative_mode": False,
            "heap_primitives": False,
            "show_only_outputs": False,
        }

    logger = PGLogger(
        options["cumulative_mode"],
        options["heap_primitives"],
        options["show_only_outputs"],
        finalizer_func,
    )

    try:
        logger._runscript(script_str)
    except bdb.BdbQuit:
        pass
    finally:
        logger.finalize()


# disables security check and returns the result of finalizer_func
# WARNING: ONLY RUN THIS LOCALLY and never over the web, since
# security checks are disabled
#
# [optional] probe_exprs is a list of strings representing
# expressions whose values to probe at each step (advanced)
def exec_script_str_local(
    script_str,
    cumulative_mode,
    heap_primitives,
    finalizer_func,
    probe_exprs=None,
    allow_all_modules=False,
):
    logger = PGLogger(
        cumulative_mode,
        heap_primitives,
        False,
        finalizer_func,
        disable_security_checks=True,
        allow_all_modules=allow_all_modules,
        probe_exprs=probe_exprs,
    )

    try:
        logger._runscript(script_str)
    except bdb.BdbQuit:
        pass
    finally:
        return logger.finalize()


def json_finalizer(input_code, output_trace):
    ret = dict(code=input_code, trace=output_trace)
    json_output = json.dumps(ret, indent=2)
    return json_output


def run(code,cumulative_mode=False, heap_primitives=False, allow_all_modules=True):
    return exec_script_str_local(code, cumulative_mode, heap_primitives, json_finalizer,allow_all_modules=allow_all_modules)

run
