import json
import os
import sys
from pathlib import Path

import requests


# ============================================================
# 경로 설정
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

GENERATED_DIR = BASE_DIR / "data" / "generated"

# 원본 생성 실패 데이터
INPUT_FILE = GENERATED_DIR / "generation_failures.jsonl"

# repair 결과 저장 폴더
REPAIR_DIR = GENERATED_DIR / "repair"

REPAIR_DIR.mkdir(
    parents=True,
    exist_ok=True
)

SUCCESS_FILE = REPAIR_DIR / "repair_success.jsonl"
FAILED_FILE = REPAIR_DIR / "repair_failed.jsonl"
ALL_RESULTS_FILE = REPAIR_DIR / "repair_results.jsonl"


# ============================================================
# Ollama 설정
# ============================================================

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://127.0.0.1:11435"    
)

#
#REPAIR_MODEL = os.getenv(
#    "REPAIR_MODEL",
#    "qwen3-coder-30b"
#)


REPAIR_MODEL = os.getenv(
    "REPAIR_MODEL",
    "qwen3-coder-30b"
)



REQUEST_TIMEOUT = 300

MAX_REPAIR_ATTEMPTS = 3


# ============================================================
# 기존 code executor import
# ============================================================

try:
    from generate_questions import execute_code
except ImportError as e:
    print(
        "[ERROR] generate_questions.py에서 "
        "execute_code를 import할 수 없습니다."
    )
    print(e)
    sys.exit(1)


# ============================================================
# JSONL 읽기
# ============================================================

def load_jsonl(path):
    items = []

    if not path.exists():
        print(f"[ERROR] 파일을 찾을 수 없습니다: {path}")
        return items

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:

        for line_number, line in enumerate(
            f,
            start=1
        ):
            line = line.strip()

            if not line:
                continue

            try:
                item = json.loads(line)
                items.append(item)

            except json.JSONDecodeError as e:
                print(
                    f"[WARNING] JSON 파싱 실패 "
                    f"(line {line_number}): {e}"
                )

    return items


# ============================================================
# JSONL 저장
# ============================================================

def append_jsonl(path, data):

    with open(
        path,
        "a",
        encoding="utf-8"
    ) as f:

        f.write(
            json.dumps(
                data,
                ensure_ascii=False
            )
            + "\n"
        )


# ============================================================
# 코드 블록 제거
# ============================================================

def clean_code_response(text):

    if text is None:
        return ""

    text = str(text).strip()

    # ```java ... ```
    if text.startswith("```"):

        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines)

    return text.strip()


# ============================================================
# Repair Prompt
# ============================================================
# ============================================================
# Repair Prompt Classification
# ============================================================

def classify_repair_type(item):
    stage = str(item.get("stage", "")).lower()
    error = str(item.get("error", "")).lower()
    code = str(item.get("code", "")).lower()

    text = f"{stage}\n{error}"

    # --------------------------------------------------------
    # 1. Timeout
    # 가장 먼저 판정
    # --------------------------------------------------------

    if (
        "execution_timeout" in text
        or "timed out" in text
        or "timeout" in text
    ):
        return "timeout"

    # --------------------------------------------------------
    # 2. Functional interface / Lambda
    # --------------------------------------------------------

    if (
        "classcastexception" in error
        and (
            "lambda" in error
            or "bifunction" in error
            or "function" in error
            or "calculator" in error
        )
    ):
        return "functional_interface"

    if (
        "bifunction" in code
        or "java.util.function" in code
    ) and (
        "classcastexception" in error
        or "incompatible types" in error
    ):
        return "functional_interface"

    # --------------------------------------------------------
    # 3. Java access control
    # --------------------------------------------------------

    if (
        "has private access" in error
        or "has protected access" in error
        or "private access" in error
        or "protected access" in error
    ):
        return "access_control"

    # --------------------------------------------------------
    # 4. Exception / runtime failure
    # --------------------------------------------------------

    if (
        stage == "run"
        and (
            "exception" in error
            or "error" in error
        )
    ):
        return "exception"

    if (
        "exception in thread" in error
        or "arithmeticexception" in error
        or "runtimeexception" in error
        or "nullpointerexception" in error
        or "indexoutofboundsexception" in error
    ):
        return "exception"

    # --------------------------------------------------------
    # 5. Method signature / overload mismatch
    # --------------------------------------------------------

    if (
        "cannot be applied to given types" in error
        or (
            "cannot find symbol" in error
            and "method" in error
        )
        or (
            "method" in error
            and (
                "required:" in error
                or "found:" in error
            )
        )
    ):
        return "method_signature"


    # --------------------------------------------------------
    # 6. Type / compile structure
    # --------------------------------------------------------

    if (
        "incompatible types" in error
        or "cannot be dereferenced" in error
        or "cannot find symbol" in error
        or "required:" in error
        or "found:" in error
    ):
        return "type_method"


    # --------------------------------------------------------
    # 7. 나머지
    # --------------------------------------------------------


# ============================================================
# Common Repair Prompt
# ============================================================
# ============================================================
# Common Repair Prompt
# ============================================================

def build_common_repair_prompt(
    language,
    stage,
    error,
    concepts_text,
    plan_text,
    code
):
    return f"""
You are a code repair specialist for programming questions used in the
Korean Engineer Information Processing practical examination.

The provided program has FAILED actual compilation or execution validation.

Your job is NOT to generate a new problem.

Repair the CURRENT source code so that it passes actual validation.

The compiler/runtime error below is authoritative.

GENERAL REQUIREMENTS:

- The final source MUST compile successfully.
- The final source MUST execute successfully.
- The final source MUST terminate normally.
- The final source MUST finish within the execution time limit.
- No uncaught exception may escape from main.
- Preserve the original problem intent.
- Preserve the required concepts.
- Preserve the original difficulty as much as possible.
- Do not unnecessarily rewrite the entire program.
- Do not remove core concepts merely to make validation pass.
- Do not return unchanged code when the current code failed validation.
- Fix the ACTUAL cause shown by the compiler/runtime error.
- Inspect the complete source after making the repair.
- Do not provide the predicted output answer.


============================================================
[MANDATORY REPAIR PROCEDURE]
============================================================

Before returning the repaired source, you MUST internally perform these steps:

1. Identify the exact compiler/runtime failure.

2. Locate the exact statement responsible for the failure.

3. Determine why that statement is illegal or unsafe in the given language.

4. Make a REAL source-code modification that removes that failure.

5. Re-check the COMPLETE source for the same class of error.

6. Mentally verify that the modified source compiles successfully.

7. Mentally trace execution and verify that the program terminates normally.


============================================================
[UNCHANGED CODE IS FORBIDDEN]
============================================================

The supplied source has already FAILED real validation.

Therefore, returning source code identical to the supplied source is ALWAYS
INVALID.

You MUST change at least one executable statement, declaration, method
signature, access path, type, or control-flow structure that is causally
related to the reported failure.

Do NOT merely:

- add comments,
- reformat code,
- change whitespace,
- rename unrelated variables,
- modify unrelated statements,
- add code after a statement that already terminates execution.

The repair must directly eliminate the reported compiler/runtime failure.


============================================================
[JAVA VALIDATION CHECKLIST]
============================================================

When the language is Java, verify ALL of the following before returning code:

1. PRIVATE ACCESS

- A subclass must NOT directly access a private field of its superclass.
- A subclass must NOT directly call a private method of its superclass.
- private members are not inherited as directly accessible subclass members.

If private access is part of the intended concept, preserve the concept by
using a legal access path such as an existing or appropriate protected/public
method instead of blindly removing private.


2. METHOD CALLS

For every relevant method call, verify:

- the compile-time type of the receiver,
- whether the method exists on that type,
- whether the method is accessible,
- the number of arguments,
- the argument types,
- overload resolution,
- inheritance relationships.

Never leave a method call that has no compatible accessible method.


3. SUPERCLASS / SUBCLASS REFERENCES

A method that exists only in a subclass cannot normally be called directly
through a superclass-typed reference.

If necessary, repair the reference type, use an appropriate cast, or repair
the call while preserving the intended polymorphism concept.


4. OVERLOADING

Every overloaded method invocation must match an accessible method signature.

For example, if a class only contains:

    speak()
    speak(String)

then:

    speak(3)

is invalid unless an appropriate speak(int) overload is added or the call is
otherwise legally repaired.

Never return an incompatible overload call unchanged.


5. EXCEPTION FLOW

A catch block does NOT catch a new exception thrown from a sibling catch block
belonging to the same try statement.

If a catch block throws a new exception, trace that NEW exception separately.

If the new exception must be handled, an enclosing try-catch structure must
actually surround the statement that throws it.


6. ERROR VS EXCEPTION

java.lang.Error is NOT a subclass of java.lang.Exception.

Therefore:

    catch (Exception e)

does NOT catch:

    throw new Error(...);

If Error is deliberately thrown, it must be handled by an appropriate
enclosing handler if normal termination is required.


7. HANDLER POSITION

A handler placed AFTER a throwing statement does not handle an exception that
already escaped before reaching that handler.

WRONG:

    throw new RuntimeException();

    try {
        ...
    } catch (RuntimeException e) {
        ...
    }

The try-catch intended to handle an exception must structurally surround the
statement that can throw it.


8. FINAL EXECUTION CHECK

Before returning the repaired Java source, mentally trace the path executed
from main.

Confirm that:

- compilation succeeds,
- all invoked methods exist,
- all accessed members are legally accessible,
- overload resolution succeeds,
- no unintended infinite loop occurs,
- no RuntimeException escapes main,
- no Error escapes main,
- execution reaches normal termination.


============================================================
[INPUT]
============================================================

[LANGUAGE]

{language}

[VALIDATION FAILURE STAGE]

{stage}

[ACTUAL COMPILER / RUNTIME ERROR]

{error}

[REQUIRED CONCEPTS]

{concepts_text}

[ORIGINAL GENERATION PLAN]

{plan_text}

[CURRENT SOURCE CODE]

{code}
""".strip()


# ============================================================
# Access Control Repair Rules
# ============================================================
def build_access_control_rules():
    return """
============================================================
[SPECIALIZED TASK: JAVA ACCESS CONTROL REPAIR]
============================================================

The confirmed failure is related to Java member accessibility.

Focus specifically on:

- private fields,
- private methods,
- protected members,
- superclass/subclass access,
- declaring-class ownership.

CRITICAL JAVA PRIVATE RULE:

A private field or private method belongs exclusively to the class
that declares it.

A subclass CANNOT directly access a private superclass member.

Adding "super." DOES NOT make private access legal.

If class A declares:

    private int y;

then BOTH of the following are illegal inside subclass B:

    return y;
    return super.y;

Likewise, if class A declares:

    private int getY() {
        return y;
    }

then BOTH of the following are illegal inside subclass B:

    return getY();
    return super.getY();

DO NOT attempt to repair private-access errors merely by adding "super.".

If a subclass needs a private value owned by its superclass,
the repair MUST occur through the declaring superclass.

Preferred repair strategies:

1. If an appropriate accessible accessor already exists in the
   superclass, use that accessor.

2. If no accessible accessor exists, add a protected accessor to the
   superclass that owns the private field.

Example:

    class A {
        private int y;

        protected int getYForSubclass() {
            return y;
        }
    }

    class B extends A {
        int calculate() {
            return getYForSubclass();
        }
    }

3. If the superclass already contains a private accessor whose purpose
   clearly matches the required operation, changing that accessor from
   private to protected may be used when it minimally preserves the
   original intent.

Do NOT make a private field public merely to make compilation succeed.

Do NOT remove the private/protected concept if access control is one
of the required concepts.

After repairing one accessibility error:

- inspect the ENTIRE source,
- find every access to the affected private/protected member,
- verify each access from the declaring class and all subclasses,
- repair ALL illegal occurrences.

Preserve the inheritance structure unless changing it is absolutely
necessary.
""".strip()


# ============================================================
# Method Signature Repair Rules
# ============================================================

def build_method_signature_rules():
    return """
============================================================
[SPECIALIZED TASK: JAVA METHOD SIGNATURE / OVERLOAD REPAIR]
============================================================

The confirmed compilation failure involves a method call whose argument
types do not match any method available on the RECEIVER'S compile-time class.

You MUST repair the mismatch. Returning unchanged code is forbidden.

PROCEDURE:

1. Locate the EXACT failing invocation reported by javac.
2. Identify the compile-time type of the receiver object.
3. List the overloads actually available to that receiver.
4. Compare the argument types with those overloads.
5. Repair the code while preserving the intended concepts, especially
   method overloading, overriding, inheritance, and polymorphism.

CRITICAL JAVA RULE:

A method declared only in a sibling class or unrelated subclass is NOT
available on another class.

Example:

class Dog {
    void speak(String s) {}
}

class Cat {
    void speak(int n) {}
}

Dog d = new Dog();
d.speak(3);       // INVALID

Cat.speak(int) does NOT make Dog.speak(int) valid.

If the problem is intended to demonstrate OVERLOADING and the failing
call is meaningful to that intent, prefer adding the missing overload
to the RECEIVER CLASS.

Example repair:

class Dog {
    void speak(String s) {}

    void speak(int times) {
        for (int i = 0; i < times; i++) {
            System.out.print("Woof");
        }
    }
}

Do NOT merely change:

    d.speak(3)

into:

    d.speak("3")

when doing so would remove or weaken the intended overload concept.

Do NOT move the invocation to an unrelated object merely to compile.

Do NOT assume a method existing in another subclass is inherited by
the receiver class.

After repairing the reported call, inspect ALL method invocations in
the source for the same signature mismatch.

FINAL REQUIREMENT:

The returned source MUST be textually different from the failed source
and MUST resolve the javac error.
""".strip()


# ============================================================
# Functional Interface Repair Rules
# ============================================================

def build_functional_interface_rules():
    return """
============================================================
[SPECIALIZED TASK: FUNCTIONAL INTERFACE / LAMBDA REPAIR]
============================================================

The confirmed failure involves Java functional-interface compatibility.

A lambda object is created according to its TARGET functional-interface type.

An existing lambda object created as one unrelated functional interface
cannot safely be converted to another unrelated functional interface by
casting.

WRONG:

    BiFunction<Integer, Integer, Integer> operation =
        (a, b) -> a + b;

    execute((Calculator) operation, 1, 2);

Do NOT repair this with:

    (Calculator) operation

or:

    (Calculator & BiFunction<Integer, Integer, Integer>) operation

These approaches may result in ClassCastException.

If execute requires Calculator, repair the TARGET TYPE of the lambda.

CORRECT PATTERN:

    Calculator operation =
        (a, b) -> a + b;

    execute(operation, 1, 2);

RULES:

- Preserve the lambda expression.
- Preserve the custom functional interface when it is a required concept.
- Prefer correcting the lambda declaration type.
- Do not use forced casts between unrelated functional interfaces.
- Do not use intersection casts to disguise an incompatible lambda object.
- Verify every method parameter type after the repair.
- The repaired code must execute without ClassCastException.
""".strip()


# ============================================================
# Exception Repair Rules
# ============================================================
def build_exception_rules():
    return """
============================================================
[SPECIALIZED TASK: EXCEPTION FLOW REPAIR]
============================================================

The confirmed failure involves runtime exception or Error propagation.

Trace the failing control flow from the EXACT statement where the
Throwable is created until program termination.

The final program MUST terminate normally.

============================================================
[1. RETHROWN EXCEPTION RULE]
============================================================

A catch block does NOT catch an exception newly thrown from another catch
block belonging to the SAME try statement.

WRONG:

    try {
        risky();
    } catch (ArithmeticException e) {
        throw new RuntimeException();
    } catch (Exception e) {
        // This does NOT catch the RuntimeException thrown above.
    }

The RuntimeException thrown inside the first catch has already left the
original try-catch structure.

If rethrowing is an intended concept, preserve the rethrow and handle it
with an OUTER try-catch.

CORRECT STRUCTURE:

    try {
        try {
            risky();
        } catch (ArithmeticException e) {
            throw new RuntimeException();
        } finally {
            // meaningful finally behavior
        }
    } catch (RuntimeException e) {
        // final handling
    }

============================================================
[2. ERROR IS NOT AN EXCEPTION]
============================================================

java.lang.Error is NOT a subclass of java.lang.Exception.

Therefore:

    catch (Exception e)

does NOT catch:

    throw new Error();

WRONG:

    try {
        throw new Error();
    } catch (Exception e) {
        // Error is NOT caught here.
    }

If the program intentionally throws Error as part of the generated
exception-flow problem, the Error must be handled before it escapes
from main.

For example, an appropriate surrounding handler may be required:

    try {
        risky();
    } catch (Error e) {
        // final handling
    }

or, only when appropriate to the intended problem structure:

    catch (Throwable e)

Do NOT blindly replace every Exception catch with Throwable.

Preserve the intended distinction between Exception and Error whenever
that distinction is part of the problem.

============================================================
[3. CATCH PLACEMENT RULE]
============================================================

A handler must surround the ACTUAL statement or control-flow path that
throws the uncaught Throwable.

Adding another try-catch AFTER an uncaught throw does NOT repair the
original failure.

WRONG:

    try {
        throw new Error();
    } catch (Exception e) {
        System.out.print("A");
    }

    try {
        throw new Error();
    } catch (Error e) {
        System.out.print("B");
    }

The second try-catch does NOT repair the first uncaught Error because
execution terminates before reaching the second try block.

The repair must handle the ORIGINAL failing path.

IMPORTANT REPAIR CONSTRAINT:

When validation reports an uncaught Throwable, the repair MUST modify
the control-flow region that contains the ORIGINAL throwing statement.

It is NOT sufficient to add a new try-catch around code that executes
after the throwing statement.

Before returning the repaired source, verify:

- Is the exact failing throw statement now lexically inside a try block
  whose catch can handle its thrown type?

OR

- Is the Throwable propagated to an OUTER try-catch that surrounds the
  block from which it escapes?

If neither condition is true, the repair is INVALID.

Example:

INVALID REPAIR:

    throw new Error("failure");

    try {
        System.out.print("after");
    } catch (Error e) {
    }

VALID REPAIR:

    try {
        try {
            risky();
        } catch (RuntimeException e) {
            throw new Error("failure");
        }
    } catch (Error e) {
        // handle the Error here
    }

The handler must be on the propagation path of the ORIGINAL Throwable.


============================================================
[4. NESTED EXCEPTION FLOW]
============================================================

When try-catch-finally structures are nested, trace each Throwable
according to the block from which it actually escapes.

If a catch block throws a NEW exception:

    catch (ArithmeticException e) {
        throw new RuntimeException();
    }

then treat RuntimeException as a new Throwable and continue tracing it
through the OUTER control-flow structure.

If a finally block throws an exception or Error, that Throwable may
replace a previously propagating exception.

Inspect finally blocks carefully.

============================================================
[5. REPAIR PRIORITY]
============================================================

When repairing exception flow:

1. Locate the exact uncaught Throwable shown by actual validation.
2. Locate the exact statement that creates or rethrows it.
3. Determine which try statement actually contains that statement.
4. Determine which catch clauses are eligible to handle it.
5. Check the Java inheritance relationship of the thrown type and
   each catch parameter type.
6. If it is rethrown from a catch block, inspect OUTER handlers.
7. If it is thrown from finally, trace it separately.
8. Ensure the Throwable is handled before it escapes from main.
9. Trace the COMPLETE modified execution path again.

============================================================
[RULES]
============================================================

- Preserve meaningful try/catch/finally behavior.
- Preserve exception rethrowing when it is a required concept.
- Preserve the distinction between Exception and Error when relevant.
- Do not simply delete throw statements to make the program terminate.
- Do not remove exception-handling concepts merely to pass validation.
- Do not add an unrelated try-catch after the failing statement.
- Do not wrap the entire program in a meaningless catch block.
- Do not blindly catch Throwable unless it is justified by the intended
  exception-flow structure.
- Do not change the exception type unnecessarily.
- No uncaught Throwable may escape from main.
- finally must retain its intended behavior.
- Trace newly thrown exceptions separately from the original exception.
- After the repair, mentally execute the COMPLETE program.
- The repaired program MUST compile, execute, and terminate normally.
""".strip()

# ============================================================
# Timeout Repair Rules
# ============================================================

def build_timeout_rules():
    return """
============================================================
[SPECIALIZED TASK: EXECUTION TIMEOUT REPAIR]
============================================================

The program timed out during actual execution.

IMPORTANT:
This is an OUTPUT-PREDICTION examination question.
The program must be small enough for a human examinee to trace manually.

For recursive programs, FIRST inspect the concrete input passed from main.

If:
- the recursion has a valid base case,
- recursive arguments move toward that base case,
- the algorithm is logically valid,
- but the input is excessively large,

then the INPUT VALUE is the defect.

DO NOT modify the recursive algorithm.

For example:

    System.out.println(process("123"));

with Fibonacci-like exponential recursion MUST be repaired by reducing
the input to a small non-trivial value, for example:

    System.out.println(process("7"));

DO NOT:

- add artificial recursion-depth parameters,
- add arbitrary cutoff conditions,
- add memoization,
- replace recursion with iteration,
- change the recurrence,
- change existing method signatures,
- add arbitrary guards such as value > 100000,
- return 0 merely to terminate execution.

PRESERVE:

- the recursive methods,
- the original recurrence,
- method overloading,
- base cases,
- Integer.valueOf,
- string conversion,
- existing method signatures.

REPAIR PRIORITY:

1. Determine whether the recursion itself terminates mathematically.
2. If yes, inspect the concrete input.
3. If the input causes exponential or excessive computation,
   reduce ONLY that input.
4. Choose a small non-trivial value suitable for manual tracing.
5. Prefer values roughly in the range 5-10 for Fibonacci-like recursion.
6. Do not alter unrelated methods.

The final program must terminate comfortably within the execution limit
AND remain suitable as an output-prediction examination question.
""".strip()

# ============================================================
# Type / Method Repair Rules
# ============================================================

def build_type_method_rules():
    return """
============================================================
[SPECIALIZED TASK: TYPE / METHOD REPAIR]
============================================================

The confirmed failure involves Java type compatibility, method invocation,
field usage, or declaration consistency.

Use the compiler error as the authoritative source.

Check:

- method name,
- method existence,
- argument count,
- argument types,
- parameter types,
- return type,
- receiving variable type,
- field type,
- array element type,
- static versus instance context,
- method accessibility.

If the compiler says:

    required: no arguments
    found: int

then repair the mismatch directly.

If an array element has type Status, do not assign it to int.

WRONG:

    int index = statuses[x];

CORRECT TYPE PRINCIPLE:

    Status target = statuses[x];

Do not use unjustified casts to hide type errors.

Do not invent nonexistent overloads merely to satisfy a bad method call
unless the overload itself is clearly part of the intended problem concept.

After repairing the reported error, inspect the entire source for other
uses of the same invalid method or type pattern.
""".strip()


# ============================================================
# General Repair Rules
# ============================================================

def build_general_rules():
    return """
============================================================
[SPECIALIZED TASK: GENERAL CODE REPAIR]
============================================================

Analyze the exact compiler/runtime error and make the smallest substantive
repair that eliminates the confirmed failure.

Check the complete source for:

- syntax errors,
- invalid string literals,
- illegal escape sequences,
- invalid method calls,
- missing symbols,
- type mismatches,
- invalid field access,
- static/instance misuse,
- overriding/overloading mistakes,
- invalid array access,
- runtime exceptions,
- non-terminating control flow.

Do not guess blindly.

Use the provided validation error to identify the actual failing expression
or control-flow path.

After repairing it, mentally validate the COMPLETE modified source.
""".strip()


# ============================================================
# Final Output Rules
# ============================================================

def build_output_rules():
    return """
============================================================
[FINAL VALIDATION]
============================================================

Before returning the source code, internally verify:

1. Does this exact source compile?
2. Does it execute successfully?
3. Does it terminate normally?
4. Does it finish within the time limit?
5. Is the reported validation failure actually fixed?
6. Did I preserve the required concepts?
7. Did I avoid introducing a new compile/runtime failure?
8. Did I inspect the entire source for repeated occurrences of the error?

If any answer is NO, repair the source again internally before returning it.

============================================================
[OUTPUT REQUIREMENT]
============================================================

Return ONLY the complete repaired source code.

Do NOT provide explanations.
Do NOT provide the predicted answer.
Do NOT return JSON.
Do NOT return Markdown.
Do NOT use code fences.
Do NOT include any text before or after the source code.
""".strip()


# ============================================================
# Repair Prompt
# ============================================================

def build_repair_prompt(item):

    language = item.get(
        "language",
        ""
    )

    stage = item.get(
        "stage",
        ""
    )

    error = item.get(
        "error",
        ""
    )

    code = item.get(
        "code",
        ""
    )

    concepts = item.get(
        "concepts",
        []
    )

    generation_plan = item.get(
        "generation_plan",
        {}
    )

    concepts_text = ", ".join(
        str(x)
        for x in concepts
    )

    plan_text = json.dumps(
        generation_plan,
        ensure_ascii=False,
        indent=2
    )

    # --------------------------------------------------------
    # 현재 오류 유형 판정
    # --------------------------------------------------------

    repair_type = classify_repair_type(item)

    # --------------------------------------------------------
    # 공통 Prompt
    # --------------------------------------------------------

    common_prompt = build_common_repair_prompt(
        language=language,
        stage=stage,
        error=error,
        concepts_text=concepts_text,
        plan_text=plan_text,
        code=code
    )

    # --------------------------------------------------------
    # 오류별 Specialized Prompt
    # --------------------------------------------------------

    if repair_type == "timeout":

        specialized_prompt = (
            build_timeout_rules()
        )

    elif repair_type == "functional_interface":

        specialized_prompt = (
            build_functional_interface_rules()
        )

    elif repair_type == "access_control":

        specialized_prompt = (
            build_access_control_rules()
        )

    elif repair_type == "method_signature":

        specialized_prompt = (
            build_method_signature_rules()
        )

    elif repair_type == "exception":

        specialized_prompt = (
            build_exception_rules()
        )

    elif repair_type == "type_method":

        specialized_prompt = (
            build_type_method_rules()
        )

    else:

        specialized_prompt = (
            build_general_rules()
        )

    # --------------------------------------------------------
    # 최종 Prompt
    # --------------------------------------------------------

    prompt = f"""
{common_prompt}

{specialized_prompt}

{build_output_rules()}
"""

    return prompt.strip()

# ============================================================
# Ollama 호출
# ============================================================

def call_repair_model(item):
    prompt = build_repair_prompt(item)

    payload = {
        "model": REPAIR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False,
        "options": {
             "num_ctx": 8192,
            "temperature": 0.1
        }
    }

    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    content = (
        data
        .get("message", {})
        .get("content", "")
    )

    return clean_code_response(content)

# ============================================================
# 단일 문제 Repair
# ============================================================

def repair_one(item, index, total):
    language = item.get("language", "")
    original_code = item.get("code", "")

    print()
    print("=" * 70)
    print(f"[REPAIR] {index}/{total}")
    print("=" * 70)
    print(f"언어: {language}")
    print(f"원본 실패 단계: {item.get('stage')}")

    current_item = dict(item)
    current_code = original_code

    attempts = []

    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):

        print()
        print("-" * 70)
        print(
            f"[ATTEMPT] "
            f"{attempt}/{MAX_REPAIR_ATTEMPTS}"
        )
        print("-" * 70)

        print("[MODEL] 코드 수리를 요청합니다...")

        # ----------------------------------------------------
        # 모델 호출
        # ----------------------------------------------------

        try:
            repaired_code = call_repair_model(
                current_item
            )

        except Exception as e:
            print("[MODEL] ❌ 모델 호출 실패")
            print(e)

            attempt_data = {
                "attempt": attempt,
                "input_stage": current_item.get(
                    "stage"
                ),
                "input_error": current_item.get(
                    "error"
                ),
                "input_code": current_code,
                "repaired_code": None,
                "validation": {
                    "success": False,
                    "stage": "model",
                    "error": str(e)
                }
            }

            attempts.append(attempt_data)

            return {
                "repair_model": REPAIR_MODEL,
                "repair_attempt": attempt,
                "original": item,
                "repaired_code": None,
                "attempts": attempts,
                "validation": {
                    "success": False,
                    "stage": "model",
                    "error": str(e)
                }
            }

        # ----------------------------------------------------
        # 빈 응답
        # ----------------------------------------------------

        if not repaired_code:
            print("[MODEL] ❌ 빈 코드 반환")

            attempt_data = {
                "attempt": attempt,
                "input_stage": current_item.get(
                    "stage"
                ),
                "input_error": current_item.get(
                    "error"
                ),
                "input_code": current_code,
                "repaired_code": "",
                "validation": {
                    "success": False,
                    "stage": "model",
                    "error": (
                        "repair 모델이 "
                        "빈 코드를 반환했습니다."
                    )
                }
            }

            attempts.append(attempt_data)

            # 다음 attempt에서 다시 시도
            current_item = dict(current_item)
            current_item["stage"] = "repair_empty"
            current_item["error"] = (
                "이전 repair에서 빈 코드를 반환했다. "
                "원본 코드의 실제 오류를 수정한 "
                "전체 코드를 반드시 반환하라."
            )

            continue

        # ----------------------------------------------------
        # 원본과 동일한 코드
        # ----------------------------------------------------

        if repaired_code.strip() == current_code.strip():

            print(
                "[MODEL] ⚠️ 현재 코드와 "
                "동일한 코드 반환"
            )

            no_change_error = (
                "이전 repair에서 수정 전 코드와 "
                "완전히 동일한 코드를 반환했다. "
                "이 코드는 실제 검증에서 실패한 코드이다. "
                "제공된 컴파일/실행 오류를 다시 분석하여 "
                "반드시 실질적인 코드 수정을 수행하라."
            )

            attempt_data = {
                "attempt": attempt,
                "input_stage": current_item.get(
                    "stage"
                ),
                "input_error": current_item.get(
                    "error"
                ),
                "input_code": current_code,
                "repaired_code": repaired_code,
                "validation": {
                    "success": False,
                    "stage": "repair_no_change",
                    "error": no_change_error
                }
            }

            attempts.append(attempt_data)

            # 마지막 시도면 종료
            if attempt == MAX_REPAIR_ATTEMPTS:
                return {
                    "repair_model": REPAIR_MODEL,
                    "repair_attempt": attempt,
                    "original": item,
                    "repaired_code": repaired_code,
                    "attempts": attempts,
                    "validation": {
                        "success": False,
                        "stage": "repair_no_change",
                        "error": no_change_error
                    }
                }

            # 다음 시도에 원래 오류 + no-change 피드백 전달
            original_error = current_item.get(
                "error",
                ""
            )

            current_item = dict(current_item)


            current_item["error"] = (
                f"{original_error}\n\n"
                "[이전 Repair 추가 실패]\n"
                f"{no_change_error}"
            )

            current_item["code"] = current_code

            continue

        # ----------------------------------------------------
        # 실제 컴파일 / 실행
        # ----------------------------------------------------

        print()
        print(
            "[VALIDATION] "
            "실제 컴파일/실행..."
        )

        try:
            execution_result = execute_code(
                language=language,
                code=repaired_code
            )

        except Exception as e:
            execution_result = {
                "success": False,
                "stage": "validator",
                "error": str(e)
            }

        # ----------------------------------------------------
        # attempt 기록
        # ----------------------------------------------------

        attempt_data = {
            "attempt": attempt,
            "input_stage": current_item.get(
                "stage"
            ),
            "input_error": current_item.get(
                "error"
            ),
            "input_code": current_code,
            "repaired_code": repaired_code,
            "validation": execution_result
        }

        attempts.append(attempt_data)

        # ----------------------------------------------------
        # 성공
        # ----------------------------------------------------

        if execution_result.get("success"):

            print(
                "[VALIDATION] "
                "✅ Repair 성공"
            )

            print("실제 출력:")

            print(
                repr(
                    execution_result.get(
                        "output",
                        ""
                    )
                )
            )

            return {
                "repair_model": REPAIR_MODEL,
                "repair_attempt": attempt,
                "original": item,
                "repaired_code": repaired_code,
                "attempts": attempts,
                "validation": execution_result
            }

        # ----------------------------------------------------
        # 실패
        # ----------------------------------------------------

        print(
            "[VALIDATION] "
            "❌ Repair 실패"
        )

        print(
            "실패 단계:",
            execution_result.get("stage")
        )

        print("오류:")

        print(
            execution_result.get("error")
        )

        # 마지막 attempt이면 종료
        if attempt == MAX_REPAIR_ATTEMPTS:

            return {
                "repair_model": REPAIR_MODEL,
                "repair_attempt": attempt,
                "original": item,
                "repaired_code": repaired_code,
                "attempts": attempts,
                "validation": execution_result
            }

        # ----------------------------------------------------
        # 다음 Repair용 데이터 구성
        # ----------------------------------------------------

        print()
        print(
            "[FEEDBACK] 검증 오류를 "
            "다음 Repair에 다시 전달합니다."
        )

        current_code = repaired_code

        current_item = dict(item)

        current_item["code"] = repaired_code

        current_item["stage"] = (
            execution_result.get(
                "stage",
                "unknown"
            )
        )

        current_item["error"] = (
            execution_result.get(
                "error",
                "알 수 없는 검증 오류"
            )
        )

    # 이론상 도달하지 않음
    return {
        "repair_model": REPAIR_MODEL,
        "repair_attempt": MAX_REPAIR_ATTEMPTS,
        "original": item,
        "repaired_code": current_code,
        "attempts": attempts,
        "validation": {
            "success": False,
            "stage": "unknown",
            "error": (
                "최대 repair 횟수를 "
                "초과했습니다."
            )
        }
    }


# ============================================================
# 이전 결과 파일 초기화
# ============================================================

def reset_output_files():

    for path in [
        SUCCESS_FILE,
        FAILED_FILE,
        ALL_RESULTS_FILE
    ]:

        if path.exists():
            path.unlink()


# ============================================================
# 통계 출력
# ============================================================

def print_summary(
    total,
    success_count,
    failed_count
):

    print()
    print()
    print("=" * 70)
    print("REPAIR BENCHMARK RESULT")
    print("=" * 70)

    print(
        f"Repair 모델 : {REPAIR_MODEL}"
    )

    print(
        f"전체 문제   : {total}"
    )

    print(
        f"성공        : {success_count}"
    )

    print(
        f"실패        : {failed_count}"
    )

    if total > 0:

        success_rate = (
            success_count
            / total
            * 100
        )

        print(
            f"성공률      : "
            f"{success_rate:.2f}%"
        )

    print()
    print(
        f"전체 결과   : {ALL_RESULTS_FILE}"
    )

    print(
        f"성공 결과   : {SUCCESS_FILE}"
    )

    print(
        f"실패 결과   : {FAILED_FILE}"
    )

    print("=" * 70)


# ============================================================
# main
# ============================================================

def main():

    print("=" * 70)
    print("CODE REPAIR BENCHMARK")
    print("=" * 70)

    print(
        f"입력 파일 : {INPUT_FILE}"
    )

    print(
        f"Repair 모델: {REPAIR_MODEL}"
    )

    print(
        f"Ollama URL : {OLLAMA_URL}"
    )

    print()

    # --------------------------------------------------------
    # 실패 데이터 로드
    # --------------------------------------------------------

    failures = load_jsonl(
        INPUT_FILE
    )

        # 최초 generation 실패만 사용
    # 기존 repair 실패(repair_no_change 등)는 벤치마크에서 제외
    failures = [
        item
        for item in failures
        if item.get("failure_type") == "generation"
    ]


    if not failures:

        print(
            "[ERROR] Repair할 실패 문제가 없습니다."
        )

        return

    print(
        f"로드된 실패 문제: "
        f"{len(failures)}개"
    )

    # --------------------------------------------------------
    # 몇 문제 테스트할지 선택
    # --------------------------------------------------------

    raw_count = input(
        "\nRepair할 문제 수 "
        f"(Enter = 전체 {len(failures)}개): "
    ).strip()

    if raw_count:

        try:

            requested_count = int(
                raw_count
            )

            if requested_count <= 0:
                raise ValueError

            failures = failures[
                :requested_count
            ]

        except ValueError:

            print(
                "[ERROR] 문제 수는 "
                "1 이상의 정수여야 합니다."
            )

            return

    total = len(failures)

    # --------------------------------------------------------
    # 기존 benchmark 결과 초기화
    # --------------------------------------------------------

    reset_output_files()

    # --------------------------------------------------------
    # Repair 실행
    # --------------------------------------------------------

    success_count = 0
    failed_count = 0

    for index, item in enumerate(
        failures,
        start=1
    ):

        result = repair_one(
            item=item,
            index=index,
            total=total
        )

        # 전체 결과
        append_jsonl(
            ALL_RESULTS_FILE,
            result
        )

        validation = result.get(
            "validation",
            {}
        )

        if validation.get("success"):

            success_count += 1

            append_jsonl(
                SUCCESS_FILE,
                result
            )

        else:

            failed_count += 1

            append_jsonl(
                FAILED_FILE,
                result
            )

    # --------------------------------------------------------
    # 결과
    # --------------------------------------------------------

    print_summary(
        total=total,
        success_count=success_count,
        failed_count=failed_count
    )


if __name__ == "__main__":
    main()