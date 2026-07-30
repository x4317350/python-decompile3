|buildstatus| |Pypi Installs| |Latest Version| |Supported Python Versions|

|packagestatus|

.. contents::

decompyle3
==========

A native Python cross-version decompiler and fragment decompiler.
A reworking of uncompyle6_.

I gave a talk on this at `BlackHat Asia 2024 <https://youtu.be/H-7ZNrpsV50?si=nOaixgYHr7RbILVS>`_.

Introduction
------------

*decompyle3* translates Python bytecode back into equivalent Python
source code. The supported target bytecodes are Python 3.7, Python 3.8,
and standard on-disk CPython 3.11 ``.pyc`` files.

For decompilation of older Python bytecode, see uncompyle6_.

Why this?
---------

Uncompyle6 is awesome, but it has a fundamental problem in the way
it handles control flow. In the early days of Python, when there was
little optimization and code was generated in a very template-oriented way, figuring out control flow structures could be done by simply looking at code patterns.

Over the years, more code optimization, specifically around handling jumps, has made it harder to support detecting control flow strictly
from code patterns. This was noticed as far back as Python 2.4 (2004), but since this is a difficult problem, so far it hasn't been tackled
in a satisfactory way.

The initial attempt to fix to this problem was to add markers in the
instruction stream, initially this was a ``COME_FROM`` instruction, and
then use that in pattern detection.

Over the years, I've extended that to be more specific, so
``COME_FROM_LOOP`` and ``COME_FROM_WITH`` were added. And I added checks
at grammar-reduce time to make try to make sure jumps match with
supposed ``COME_FROM`` targets.

However, all of this is complicated, not robust, has greatly slowed down deparsing and is not really tenable.

In this project, we began rewriting and refactoring the grammar.

However, even this isn't enough. Control flow needs
to be addressed by using dominators and reverse-dominators, which the python-control-flow_ project can give.

This I am *finally* slowly doing in yet another non-public project. It
is a lot of work.  Funding in the form of sponsorship while greatly
appreciated isn't commensurate with the amount of effort, and
currently I have a full-time job. So it may take time before it is
available publicly, if at all.

Requirements and Supported Versions
-----------------------------------

The interpreter running *decompyle3* and the interpreter that produced the
target bytecode are separate versions.

* Python 3.7 and 3.8 target bytecode can be processed while running
  *decompyle3* on Python 3.7 or newer.
* CPython 3.11 target bytecode requires running *decompyle3* on Python 3.11
  or newer.
* PyPy 3.11 and non-CPython 3.11 bytecode are not supported.

The CPython 3.11 path covers functions, classes, expressions, ordinary
control flow, comprehensions, generators and coroutines, exception-table
statements, ``with``/``async with``, ``match``/``case``, and the documented
``except*`` protocol shapes. See `CPython 3.11 support and limitations
<PYTHON_311_SUPPORT.md>`_ for the exact scope and fail-closed limitations.

Installation
------------

You can install from PyPI using the name ``decompyle3``::

    pip install decompyle3


To install from source code, this project uses setup.py, so it follows the standard Python routine::

    $ pip install -e .  # set up to run from source tree

or::

    $ python setup.py install # may need sudo

A GNU Makefile is also provided, so :code:`make install` (possibly as root or
sudo) will do the steps above.

Running Tests
-------------

::

   make check

A GNU makefile has been added to smooth over setting up and running the right
command, and running tests from fastest to slowest.

If you have remake_ installed, you can see the list of all tasks
including tests via :code:`remake --tasks`


Usage
-----

Run

::

$ decompyle3 *compiled-python-file-pyc-or-pyo*

For usage help:

::

   $ decompyle3 -h

To process a batch into an output directory and validate generated syntax::

   $ decompyle3 --output recovered --verify syntax first.pyc second.pyc

A batch continues after an individual input failure and returns a non-zero
status if any input or verification failed. Partial output is marked with an
``_failed`` suffix.

Verification
------------

If you want Python syntax and recompilation verification of the
decompilation result, add :code:`--verify syntax`. The running interpreter
must understand the target source syntax. Use :code:`--verify run` only when
executing the generated program is safe and its target version matches the
running interpreter.

You can also cross-compare the results with another Python decompiler
like unpyc37_ . Since they work differently, bugs here often aren't in
that, and vice versa.

There is an interesting class of these programs that is readily
available to give stronger verification: those programs that, when run, test themselves. Our test suite includes these.

And Python comes with another set of programs like this: its test
suite for the standard library. We have some code in :code:`test/stdlib` to
facilitate this kind of checking too.

Known Bugs/Restrictions
-----------------------

**We support only released versions, not candidate versions.** Note however
that the magic of a released version is usually the same as the *last* candidate version prior to release.

We also don't handle PJOrion_ or otherwise obfuscated code. For
PJOrion try: PJOrion Deobfuscator_ to unscramble the bytecode to get
valid bytecode before trying this tool; pydecipher_ might help with that.

This program can't decompile Microsoft Windows EXE files created by
Py2EXE_, although we can probably decompile the code after you extract
the bytecode properly. `Pydeinstaller <https://github.com/charles-dyfis-net/pydeinstaller>`_ may help with unpacking Pyinstaller bundlers.

Handling pathologically long lists of expressions or statements is slow. We don't handle Cython_ or MicroPython, which don't use bytecode.

CPython 3.11 support is deliberately fail closed for structures that cannot
yet be recovered reliably. This includes some uncommon stack manipulation
and mapping-building layouts, and ``except*`` combined with an ``else`` suite
or an enclosing ``finally``. See `CPython 3.11 support and limitations
<PYTHON_311_SUPPORT.md>`_ for the maintained list.

There are numerous bugs in decompilation. And that's true for every
other CPython decompilers I have encountered, even the ones that
claimed to be "perfect" on some particular version like 2.4.

As Python progresses, decompilation also gets harder because the
compilation is more sophisticated and the language itself is more
sophisticated. This makes syntax, recompilation, and behavior regression
tests essential even when generated source looks plausible.

You can easily find bugs by running the tests against the standard
test suite that Python uses to check itself. At any given time, there are
dozens of known problems that are pretty well isolated and that could
be solved if one were to put in the time to do so. The problem is that
there aren't that many people who have been working on bug fixing.

You may run across a bug, that you want to report. Please do so. But
be aware that it might not get my attention for a while. If you
sponsor or support the project in some way, I'll prioritize your
issues above the queue of other things I might be doing instead. In
rare situations, I can do a hand decompilation of bytecode for a fee.
However, this is expensive, usually beyond what most people are willing
to spend.

See Also
--------

* https://github.com/andrew-tavera/unpyc37/ : indirect fork of https://code.google.com/archive/p/unpyc3/ The above projects use a different decompiling technique than what is used here. Instructions are walked. Some instructions use the stack to generate strings, while others don't. Because control flow isn't dealt with directly, it too suffers the same problems as the various ``uncompyle`` and ``decompyle`` programs.
* https://github.com/rocky/python-xdis : Cross Python version disassembler
* https://github.com/rocky/python-xasm : Cross Python version assembler
* https://github.com/rocky/python-decompile3/wiki : Wiki Documents that describe the code and aspects of it in more detail

.. |buildstatus| image:: https://dl.circleci.com/status-badge/img/gh/rocky/python-decompile3/tree/master.svg?style=svg
        :target: https://dl.circleci.com/status-badge/redirect/gh/rocky/python-decompile3/tree/master
.. |packagestatus| image:: https://repology.org/badge/vertical-allrepos/python:uncompyle6.svg
		 :target: https://repology.org/project/python:decompyle3/versions
.. _Cython: https://en.wikipedia.org/wiki/Cython
.. _MicroPython: https://micropython.org
.. _uncompyle6: https://pypi.python.org/pypi/uncompyle6
.. _python-control-flow: https://github.com/rocky/python-control-flow
.. _trepan: https://pypi.python.org/pypi/trepan3k
.. _compiler: https://pypi.python.org/pypi/spark_parser
.. _HISTORY: https://github.com/rocky/python-decompile3/blob/master/HISTORY.md
.. _debuggers: https://pypi.python.org/pypi/trepan3k
.. _remake: https://bashdb.sf.net/remake
.. _unpyc37: https://github.com/andrew-tavera/unpyc37/
.. _this: https://github.com/rocky/python-decompile3/wiki/Deparsing-technology-and-its-use-in-exact-location-reporting
.. |TravisCI| image:: https://travis-ci.org/rocky/python-decompile3.svg
		 :target: https://travis-ci.org/rocky/python-decompile3
.. |CircleCI| image:: https://circleci.com/gh/rocky/python-decompile3.svg?style=svg
	  :target: https://circleci.com/gh/rocky/python-decompile3

.. _PJOrion: http://www.koreanrandom.com/forum/topic/15280-pjorion-%D1%80%D0%B5%D0%B4%D0%B0%D0%BA%D1%82%D0%B8%D1%80%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5-%D0%BA%D0%BE%D0%BC%D0%BF%D0%B8%D0%BB%D1%8F%D1%86%D0%B8%D1%8F-%D0%B4%D0%B5%D0%BA%D0%BE%D0%BC%D0%BF%D0%B8%D0%BB%D1%8F%D1%86%D0%B8%D1%8F-%D0%BE%D0%B1%D1%84
.. _Deobfuscator: https://github.com/extremecoders-re/PjOrion-Deobfuscator
.. _Py2EXE: https://en.wikipedia.org/wiki/Py2exe
.. |Supported Python Versions| image:: https://img.shields.io/pypi/pyversions/decompyle3.svg
.. |Latest Version| image:: https://badge.fury.io/py/decompyle3.svg
		 :target: https://badge.fury.io/py/decompyle3
.. |PyPI Installs| image:: https://pepy.tech/badge/decompyle3/month
.. _pydecipher: https://github.com/mitre/pydecipher
