#!/usr/bin/env python
# Copyright (c) 2017 Trail of Bits, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import collections
import logging
import manticore
import multiprocessing
import sys
import traceback
from .common import DeepState

from manticore.core.state import TerminateState
from manticore.utils.helpers import issymbolic


L = logging.getLogger("deepstate.mcore")
L.setLevel(logging.INFO)

OUR_TERMINATION_REASON = "I DeepState'd it"

class DeepManticore(DeepState):
  def __init__(self, state):
    super(DeepManticore, self).__init__()
    self.state = state

  def __del__(self):
    self.state = None

  def get_context(self):
    return self.state.context

  def is_symbolic(self, val):
    return manticore.utils.helpers.issymbolic(val)

  def create_symbol(self, name, size_in_bits):
    return self.state.new_symbolic_value(size_in_bits, name)

  def read_uintptr_t(self, ea, concretize=True, constrain=False):
    addr_size_bits = self.state.cpu.address_bit_size
    next_ea = ea + (addr_size_bits // 8)
    val = self.state.cpu.read_int(ea, size=addr_size_bits)
    if concretize:
      val = self.concretize(val, constrain=constrain)
    return val, next_ea

  def read_uint64_t(self, ea, concretize=True, constrain=False):
    val = self.state.cpu.read_int(ea, size=64)
    if concretize:
      val = self.concretize(val, constrain=constrain)
    return val, ea + 8

  def read_uint32_t(self, ea, concretize=True, constrain=False):
    val = self.state.cpu.read_int(ea, size=32)
    if concretize:
      val = self.concretize(val, constrain=constrain)
    return val, ea + 4

  def read_uint8_t(self, ea, concretize=True, constrain=False):
    val = self.state.cpu.read_int(ea, size=8)
    if concretize:
      val = self.concretize(val, constrain=constrain)
    if isinstance(val, str):
      assert len(val) == 1
      val = ord(val)
    return val, ea + 1

  def write_uint8_t(self, ea, val):
    self.state.cpu.write_int(ea, val, size=8)
    return ea + 1

  def concretize(self, val, constrain=False):
    if isinstance(val, (int, long)):
      return val
    elif isinstance(val, str):
      assert len(val) == 1
      return ord(val[0])

    assert self.is_symbolic(val)
    concrete_val = self.state.solve_one(val)
    if isinstance(concrete_val, str):
      assert len(concrete_val) == 1
      concrete_val = ord(concrete_val[0])
    if constrain:
      self.add_constraint(val == concrete_val)
    return concrete_val

  def concretize_min(self, val, constrain=False):
    if isinstance(val, (int, long)):
      return val
    concrete_val = self.state.solve_n(val)
    if constrain:
      self.add_constraint(val == concrete_val)
    return concrete_val

  def concretize_many(self, val, max_num):
    assert 0 < max_num
    if isinstance(val, (int, long)):
      return [val]
    return self.state.solver.eval_upto(val, max_num)

  def add_constraint(self, expr):
    if self.is_symbolic(expr):
      self.state.constrain(expr)
      # TODO(pag): How to check satisfiability?
    return True

  def pass_test(self):
    super(DeepManticore, self).pass_test()
    raise TerminateState(OUR_TERMINATION_REASON, testcase=False)

  def fail_test(self):
    super(DeepManticore, self).fail_test()
    raise TerminateState(OUR_TERMINATION_REASON, testcase=False)

  def abandon_test(self):
    super(DeepManticore, self).abandon_test()
    raise TerminateState(OUR_TERMINATION_REASON, testcase=False)


def hook_IsSymbolicUInt(state, arg):
  """Implements DeepState_IsSymblicUInt, which returns 1 if its input argument
  has more then one solutions, and zero otherwise."""
  return DeepManticore(state).api_is_symbolic_uint(arg)


def hook_Assume(state, arg):
  """Implements _DeepState_Assume, which tries to inject a constraint."""
  DeepManticore(state).api_assume(arg)


def hook_StreamInt(state, level, format_ea, unpack_ea, uint64_ea):
  """Implements _DeepState_StreamInt, which gives us an integer to stream, and
  the format to use for streaming."""
  DeepManticore(state).api_stream_int(level, format_ea, unpack_ea, uint64_ea)


def hook_StreamFloat(state, level, format_ea, unpack_ea, double_ea):
  """Implements _DeepState_StreamFloat, which gives us an double to stream, and
  the format to use for streaming."""
  DeepManticore(state).api_stream_float(level, format_ea, unpack_ea, double_ea)


def hook_StreamString(state, level, format_ea, str_ea):
  """Implements _DeepState_StreamString, which gives us an double to stream, and
  the format to use for streaming."""
  DeepManticore(state).api_stream_string(level, format_ea, str_ea)


def hook_LogStream(state, level):
  """Implements DeepState_LogStream, which converts the contents of a stream for
  level `level` into a log for level `level`."""
  DeepManticore(state).api_log_stream(level)


def hook_Pass(state):
  """Implements DeepState_Pass, which notifies us of a passing test."""
  DeepManticore(state).api_pass()


def hook_Fail(state):
  """Implements DeepState_Fail, which notifies us of a passing test."""
  DeepManticore(state).api_fail()


def hook_Abandon(state, reason):
  """Implements DeepState_Abandon, which notifies us that a problem happened
  in DeepState."""
  DeepManticore(state).api_abandon(reason)


def hook_SoftFail(state):
  """Implements DeepState_Fail, which notifies us of a passing test."""
  DeepManticore(state).api_soft_fail()


def hook_ConcretizeData(state, begin_ea, end_ea):
  """Implements the `Deeptate_ConcretizeData` API function, which lets the
  programmer concretize some data in the exclusive range
  `[begin_ea, end_ea)`."""
  return DeepManticore(state).api_concretize_data(begin_ea, end_ea)


def hook_ConcretizeCStr(state, begin_ea):
  """Implements the `Deeptate_ConcretizeCStr` API function, which lets the
    programmer concretize a NUL-terminated string starting at `begin_ea`."""
  return DeepManticore(state).api_concretize_cstr(begin_ea)


def hook_Log(state, level, ea):
  """Implements DeepState_Log, which lets Manticore intercept and handle the
  printing of log messages from the simulated tests."""
  DeepManticore(state).api_log(level, ea)


def hook(func):
  return lambda state: state.invoke_model(func)


def done_test(_, state, state_id, reason):
  """Called when a state is terminated."""
  if OUR_TERMINATION_REASON not in reason:
    L.error("State {} terminated for unknown reason: {}".format(
        state_id, reason))
    return
  mc = DeepManticore(state)
  mc.report()


def do_run_test(state, apis, test):
  """Run an individual test case."""
  state.cpu.PC = test.ea
  m = manticore.Manticore(state, sys.argv[1:])
  m.verbosity(1)

  state = m.initial_state
  mc = DeepManticore(state)
  mc.begin_test(test)
  del mc
  
  m.add_hook(apis['IsSymbolicUInt'], hook(hook_IsSymbolicUInt))
  m.add_hook(apis['ConcretizeData'], hook(hook_ConcretizeData))
  m.add_hook(apis['ConcretizeCStr'], hook(hook_ConcretizeCStr))
  m.add_hook(apis['Assume'], hook(hook_Assume))
  m.add_hook(apis['Pass'], hook(hook_Pass))
  m.add_hook(apis['Fail'], hook(hook_Fail))
  m.add_hook(apis['SoftFail'], hook(hook_SoftFail))
  m.add_hook(apis['Abandon'], hook(hook_Abandon))
  m.add_hook(apis['Log'], hook(hook_Log))
  m.add_hook(apis['StreamInt'], hook(hook_StreamInt))
  m.add_hook(apis['StreamFloat'], hook(hook_StreamFloat))
  m.add_hook(apis['StreamString'], hook(hook_StreamString))
  m.add_hook(apis['LogStream'], hook(hook_LogStream))

  m.subscribe('will_terminate_state', done_test)
  m.run()


def run_test(state, apis, test):
  try:
    do_run_test(state, apis, test)
  except:
    L.error("Uncaught exception: {}\n{}".format(
        sys.exc_info()[0], traceback.format_exc()))


def run_tests(args, state, apis):
  """Run all of the test cases."""
  pool = multiprocessing.Pool(processes=max(1, args.num_workers))
  results = []
  mc = DeepManticore(state)
  tests = mc.find_test_cases()
  
  L.info("Running {} tests across {} workers".format(
      len(tests), args.num_workers))

  for test in tests:
    res = pool.apply_async(run_test, (state, apis, test))
    results.append(res)

  pool.close()
  pool.join()

  exit(0)


def main():
  parser = argparse.ArgumentParser(
      description="Symbolically execute unit tests with Manticore")

  parser.add_argument(
      "--num_workers", default=1, type=int,
      help="Number of workers to spawn for testing and test generation.")

  parser.add_argument(
      "binary", type=str, help="Path to the test binary to run.")

  args = parser.parse_args()

  m = manticore.Manticore(args.binary)
  m.verbosity(1)

  # Hack to get around current broken _get_symbol_address 
  m._binary_type = 'not elf'
  m._binary_obj = m._initial_state.platform.elf

  setup_ea = m._get_symbol_address('DeepState_Setup')
  setup_state = m._initial_state

  mc = DeepManticore(setup_state)

  ea_of_api_table = m._get_symbol_address('DeepState_API')
  apis = mc.read_api_table(ea_of_api_table)
  del mc
  m.add_hook(setup_ea, lambda state: run_tests(args, state, apis))
  m.run()


if "__main__" == __name__:
  exit(main())