#!/usr/bin/env python
# Copyright (c) 2018 Trail of Bits, Inc.
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

from __future__ import print_function
import argparse
import subprocess
import os
import sys
import time


def main():
  global candidateRuns, currentTest, s, passStart

  parser = argparse.ArgumentParser(description="Intelligently reduce test case")

  parser.add_argument(
    "binary", type=str, help="Path to the test binary to run.")
  parser.add_argument(
    "input_test", type=str, help="Path to test to reduce.")
  parser.add_argument(
    "output_test", type=str, help="Path for reduced test.")
  parser.add_argument(
    "--which_test", type=str, help="Which test to run (equivalent to --input_which_test).", default=None)
  parser.add_argument(
    "--criteria", type=str, help="String to search for in valid reduction outputs.",
    default=None)
  parser.add_argument(
    "--search", action="store_true", help="Allow initial test to not satisfy criteria (search for test).",
    default=None)
  parser.add_argument(
    "--timeout", type=int, help="After this amount of time (in seconds), give up on reduction.",
    default=1200)
  parser.add_argument(
    "--maxByteRange", type=int, help="Maximum size of byte chunk to try in range removals.",
    default=16)
  parser.add_argument(
    "--fast", action='store_true',
    help="Faster, less complete, reduction (no byte range removal pass).")
  parser.add_argument(
    "--slow", action='store_true',
    help="Slower, more complete, reduction (byte pattern pass).")
  parser.add_argument(
    "--slowest", action='store_true',
    help="Slowest, most complete, reduction (byte pattern pass, tries all byte ranges).")
  parser.add_argument(
    "--verbose", action='store_true',
    help="Verbose reduction.")
  parser.add_argument(
    "--fork", action='store_true',
    help="Fork when running.")
  parser.add_argument(
    "--noStructure", action='store_true',
    help="Don't use test structure.")
  parser.add_argument(
    "--noPad", action='store_true',
    help="Don't pad test with zeros.")

  class TimeoutException(Exception):
    pass

  args = parser.parse_args()

  maxByteRange = args.maxByteRange
  deepstate = args.binary
  test = args.input_test
  out = args.output_test
  checkString = args.criteria
  whichTest = args.which_test

  start = time.time()
  candidateRuns = 0

  def runCandidate(candidate):
    global candidateRuns

    candidateRuns += 1
    if (time.time() - start) > args.timeout:
      raise TimeoutException
    with open(".reducer." + str(os.getpid()) + ".out", 'w') as outf:
      cmd = [deepstate + " --input_test_file " +
           candidate + " --verbose_reads"]
      if whichTest is not None:
        cmd += ["--input_which_test", whichTest]
      if not args.fork:
        cmd += ["--no_fork"]
      subprocess.call(cmd, shell=True, stdout=outf, stderr=outf)
    result = []
    with open(".reducer." + str(os.getpid()) + ".out", 'r') as inf:
      for line in inf:
        result.append(line)
    return result

  def checks(result):
    for line in result:
      if checkString:
        if checkString in line:
          return True
      else:
        if "ERROR: Failed:" in line:
          return True
        if "ERROR: Crashed" in line:
          return True
    return False

  def writeAndRunCandidate(test):
    with open(".candidate." + str(os.getpid()) + ".test", 'wb') as outf:
      outf.write(test)
    r = runCandidate(".candidate." + str(os.getpid()) + ".test")
    return r

  def structure(result):
    if args.noStructure:
      return ([], len(currentTest))
    OneOfs = []
    currentOneOf = []
    for line in result:
      if "STARTING OneOf CALL" in line:
        currentOneOf.append(-1)
      elif "Reading byte at" in line:
        lastRead = int(line.split()[-1])
        if len(currentOneOf) > 0:
          if currentOneOf[-1] == -1:
            currentOneOf[-1] = lastRead
      elif "FINISHED OneOf CALL" in line:
        OneOfs.append((currentOneOf[-1], lastRead))
        currentOneOf = currentOneOf[:-1]
    return (OneOfs, lastRead)

  def rangeConversions(result):
    conversions = []
    startedMulti = False
    multiFirst = None
    for line in result:
      if "Reading byte at" in line:
        lastRead = int(line.split()[-1])
      if "STARTING MULTI-BYTE READ" in line:
        startedMulti = True
      if startedMulti and (multiFirst is None) and ("Reading byte at" in line):
        multiFirst = lastRead
      if "FINISHED MULTI-BYTE READ" in line:
        currentMulti = (multiFirst, lastRead)
        startedMulti = False
        multiFirst = None
      if "Converting out-of-range value" in line:
        conversions.append((currentMulti, int(line.split()[-1])))
    return conversions

  def fixRangeConversions(test, conversions):
    if args.noStructure:
      return
    numConversions = 0
    for (pos, value) in conversions:
      if pos[1] >= len(test):
        break
      if (value < 255) and (value < test[pos[1]]):
        numConversions += 1
        for b in range(pos[0], pos[1]):
          test[b] = 0
        test[pos[1]] = value
    if numConversions > 0:
      print("Applied", numConversions, "range conversions")

  initial = runCandidate(test)
  if (not args.search) and (not checks(initial)):
    print("STARTING TEST DOES NOT SATISFY REDUCTION CRITERIA!")
    return 1

  with open(test, 'rb') as test:
    currentTest = bytearray(test.read())
  original = bytearray(currentTest)

  print("Original test has", len(currentTest), "bytes")
  if args.slowest:
    maxByteRange = len(currentTest)

  fixRangeConversions(currentTest, rangeConversions(initial))
  r = writeAndRunCandidate(currentTest)
  assert(checks(r))

  s = structure(initial)
  if (s[1]+1) < len(currentTest):
    print("Last byte read:", s[1])
    print("Shrinking to ignore unread bytes")
    currentTest = currentTest[:s[1]+1]

  if currentTest != original:
    print("Writing reduced test with", len(currentTest), "bytes to", out)
    with open(out, 'wb') as outf:
      outf.write(currentTest)

  initialSize = float(len(currentTest))
  iteration = 0

  def updateCurrent(newTest):
      global currentTest, s
      currentTest = newTest
      fixRangeConversions(currentTest, rangeConversions(r))
      print("Writing reduced test with", len(currentTest), "bytes to", out)
      with open(out, 'wb') as outf:
        outf.write(currentTest)
      s = structure(r)
      percent = 100.0 * ((initialSize - len(currentTest)) / initialSize)
      print(round(time.time()-start, 2), "secs /",
              candidateRuns, "execs /", str(round(percent, 2)) + "% reduction")
      print("="*80)
      sys.stdout.flush()

  def passInfo():
      global passStart
      percent = 100.0 * ((initialSize - len(currentTest)) / initialSize)
      print("PASS FINISHED IN", round(time.time() - passStart, 2), "SECONDS, RUN:", round(time.time()-start, 2),
                "secs /", candidateRuns, "execs /", str(round(percent, 2)) + "% reduction")
      passStart = time.time()

  oldTest = []
  lastOneOfRemovalTest = []
  lastChunkRemovalTest = {}
  lastChunkRemovalTest[1] = []
  lastChunkRemovalTest[4] = []
  lastChunkRemovalTest[8] = []
  lastReduceAndDeleteTest = {}
  lastReduceAndDeleteTest[1] = []
  lastReduceAndDeleteTest[4] = []
  lastReduceAndDeleteTest[8] = []
  lastAllRangeTest = []
  lastOneOfSwapTest = []
  lastByteReduceTest = []
  lastPatternSearchTest = []

  passStart = time.time()
  try:
    while oldTest != currentTest:
      oldTest = bytearray(currentTest)

      iteration += 1
      percent = 100.0 * ((initialSize - len(currentTest)) / initialSize)
      print("=" * 80)
      print("Iteration #" + str(iteration), round(time.time()-start, 2), "secs /",
              candidateRuns, "execs /", str(round(percent, 2)) + "% reduction")

      if currentTest != lastOneOfRemovalTest:
        if args.verbose:
          print("*"*80+"\nPASS: removing OneOfs...")
        changed = True
        while changed:
          changed = False
          cuts = s[0]
          for c in cuts:
            newTest = currentTest[:c[0]] + currentTest[c[1]+1:]
            if len(newTest) == len(currentTest):
              continue # Ignore non-shrinking reductions
            r = writeAndRunCandidate(newTest)
            if checks(r):
              print("OneOf removal reduced test to", len(newTest), "bytes")
              changed = True
              updateCurrent(newTest)
              break
        lastOneOfRemovalTest = bytearray(currentTest)
        passInfo()

      for k in [1, 4, 8]:
        if currentTest != lastChunkRemovalTest[k]:
          if args.verbose:
            print("*"*80+"\nPASS: trying", k, "byte chunk removals...")
          changed = True
          startingPos = 0
          while changed:
            changed = False
            for b in range(startingPos, len(currentTest)):
              newTest = currentTest[:b] + currentTest[b+k:]
              r = writeAndRunCandidate(newTest)
              if checks(r):
                print("Removed", k, "byte(s) @", str(b) + ": reduced test to", len(newTest), "bytes")
                changed = True
                updateCurrent(newTest)
                startingPos = b
                break
            if not changed:
              for b in range(0, startingPos):
                newTest = currentTest[:b] + currentTest[b+k:]
                r = writeAndRunCandidate(newTest)
                if checks(r):
                  print("Removed", k, "byte(s) @", str(b) + ": reduced test to", len(newTest), "bytes")
                  changed = True
                  updateCurrent(newTest)
                  startingPos = b
                  break
          lastChunkRemovalTest[k] = bytearray(currentTest)
          passInfo()

      for k in [1, 4, 8]:
        if currentTest != lastReduceAndDeleteTest[k]:
          if args.verbose:
            print("*"*80+"\nPASS: byte reduce and delete", str(k) + "...")
          changed = True
          while changed:
            changed = False
            for b in range(0, len(currentTest)-k):
              if currentTest[b] == 0:
                continue
              newTest = bytearray(currentTest)
              newTest[b] = currentTest[b]-1
              newTest = newTest[:b+1] + newTest[b+k+1:]
              r = writeAndRunCandidate(newTest)
              if checks(r):
                print("Reduced byte", b, "by 1 and deleted", k, "bytes, reducing test to", len(newTest), "bytes")
                changed = True
                updateCurrent(newTest)
                break
          lastReduceAndDeleteTest[k] = bytearray(currentTest)
          passInfo()

      if not args.fast:
        if currentTest != lastAllRangeTest:
          if args.verbose:
            print("*"*80+"\nPASS: trying all byte range removals...")
          changed = True
          startingPos = 0
          while changed:
            changed = False
            for b in range(startingPos, len(currentTest)):
              if args.verbose:
                print("Trying byte range removal from", str(b) + "...")
              for v in range(b+2, min(len(currentTest), b+maxByteRange)):
                if (v-b) in [4, 8]:
                  continue
                newTest = currentTest[:b] + currentTest[v:]
                r = writeAndRunCandidate(newTest)
                if checks(r):
                  print("Byte range removal of bytes", str(b) + "-" + str(v-1),
                          "reduced test to", len(newTest), "bytes")
                  changed = True
                  updateCurrent(newTest)
                  startingPos = b
                  break
              if changed:
                break
            if not changed:
              for b in range(0, startingPos):
                if args.verbose:
                  print("Trying byte range removal from", str(b) + "...")
                for v in range(b+2, min(len(currentTest), b+maxByteRange)):
                  if (v-b) in [4, 8]:
                    continue
                  newTest = currentTest[:b] + currentTest[v:]
                  r = writeAndRunCandidate(newTest)
                  if checks(r):
                    print("Byte range removal of bytes", str(b) + "-" + str(v-1),
                            "reduced test to", len(newTest), "bytes")
                    changed = True
                    updateCurrent(newTest)
                    startingPos = b
                    break
                if changed:
                  break
          lastAllRangeTest = bytearray(currentTest)
          passInfo()

      if currentTest != lastOneOfSwapTest:
        if args.verbose:
          print("*"*80+"\nPASS: swapping OneOfs...")
        changed = True
        while changed:
          changed = False
          cuts = s[0]
          for i in range(len(cuts)-1):
            cuti = cuts[i]
            bytesi = currentTest[cuti[0]:cuti[1] + 1]
            if args.verbose:
              print("Trying OneOf swaps from byte", cuti[0], "[" + " ".join(map(str, bytesi)) + "]")
            for j in range(i + 1, len(cuts)):
              cutj = cuts[j]
              if cutj[0] > cuti[1]:
                bytesj = currentTest[cutj[0]:cutj[1] + 1]
                if (len(bytesj) > 0) and (bytesi > bytesj):
                  newTest = currentTest[:cuti[0]] + bytesj + currentTest[cuti[1]+1:cutj[0]]
                  newTest += bytesi
                  newTest += currentTest[cutj[1]+1:]
                  newTest = bytearray(newTest)
                  r = writeAndRunCandidate(newTest)
                  if checks(r):
                    print("OneOf swap @ byte", cuti[0], "[" + " ".join(map(str, bytesi)) + "]", "with",
                            cutj[0], "[" + " ".join(map(str, bytesj)) + "]")
                    changed = True
                    updateCurrent(newTest)
                    break
              if changed:
                break
            if changed:
              break
        lastOneOfSwapTest = bytearray(currentTest)
        passInfo()

      if currentTest != lastByteReduceTest:
          if args.verbose:
              print("*"*80+"\nPASS: byte reductions...")
          changed = True
          startingPos = 0
          while changed:
            changed = False
            for b in range(startingPos, len(currentTest)):
              for v in range(0, currentTest[b]):
                newTest = bytearray(currentTest)
                newTest[b] = v
                r = writeAndRunCandidate(newTest)
                if checks(r):
                  print("Reduced byte", b, "from", currentTest[b], "to", v)
                  changed = True
                  updateCurrent(newTest)
                  startingPos = b+1
                  break
              if changed:
                break
            if changed:
              continue
            for b in range(0, startingPos):
              for v in range(0, currentTest[b]):
                newTest = bytearray(currentTest)
                newTest[b] = v
                r = writeAndRunCandidate(newTest)
                if checks(r):
                  print("Reduced byte", b, "from", currentTest[b], "to", v)
                  changed = True
                  updateCurrent(newTest)
                  startingPos = b+1
                  break
              if changed:
                break
          lastByteReduceTest = bytearray(currentTest)
          passInfo()

      if (args.slow or args.slowest) and (oldTest == currentTest):
        if currentTest != lastPatternSearchTest:
          if args.verbose:
            print("*"*80+"\nPASS: byte pattern search...")
          changed = True
          while changed:
            changed = False
            for b1 in range(0, len(currentTest)-4):
              if args.verbose:
                print("Trying byte pattern search from byte", str(b1) + "...")
              for b2 in range(b1+2, len(currentTest)-4):
                v1 = (currentTest[b1], currentTest[b1+1])
                v2 = (currentTest[b2], currentTest[b2+1])
                if (v1 == v2):
                  ba = bytearray(v1)
                  part1 = currentTest[:b1]
                  part2 = currentTest[b1+2:b2]
                  part3 = currentTest[b2+2:]
                  banews = []
                  banews.append(ba[0:1])
                  banews.append(ba[1:2])
                  if ba[0] > 0:
                    for v in range(0, ba[0]):
                      banews.append(bytearray([v, ba[1]]))
                    banews.append(bytearray([ba[0]-1]))
                  if ba[1] > 0:
                    for v in range(0, ba[1]):
                      banews.append(bytearray([ba[0], v]))
                  for banew in banews:
                    newTest = part1 + banew + part2 + banew + part3
                    r = writeAndRunCandidate(newTest)
                    if checks(r):
                      print("Byte pattern", tuple(ba), "at", b1, "and", b2, "changed to", tuple(banew))
                      changed = True
                      updateCurrent(newTest)
                      break
                  if changed:
                    break
              if changed:
                break
          lastPatternSearchTest = bytearray(currentTest)
          passInfo()

        if oldTest == currentTest:
          print("*" * 80)
          print("DONE: NO (MORE) REDUCTIONS FOUND")
  except TimeoutException:
    print("*" * 80)
    print("DONE: REDUCTION TIMED OUT AFTER", args.timeout, "SECONDS")

  print("=" * 80)
  percent = 100.0 * ((initialSize - len(currentTest)) / initialSize)
  print("Completed", iteration, "iterations:", round(time.time()-start, 2), "secs /",
          candidateRuns, "execs /", str(round(percent, 2)) + "% reduction")

  if not args.noPad:
    if (s[1] + 1) > len(currentTest):
      print("Padding test with", (s[1] + 1) - len(currentTest), "zeroes")
      padding = bytearray('\x00' * ((s[1] + 1) - len(currentTest)), 'utf-8')
      currentTest = currentTest + padding
  
  print("Writing reduced test with", len(currentTest), "bytes to", out)
    
  with open(out, 'wb') as outf:
    outf.write(currentTest)

  return 0

if "__main__" == __name__:
  exit(main())
