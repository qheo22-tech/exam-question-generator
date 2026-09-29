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
    "qwen3-32b"
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

    prompt = f"""
You are a code repair specialist for programming questions used in the
Korean Engineer Information Processing practical examination.

Your task is NOT to generate a new programming problem.

Your task is to repair the provided source code so that it passes actual
compilation and execution validation while preserving the original problem
intent, difficulty, structure, and required concepts.

The compiler/runtime feedback provided below is authoritative.


============================================================
[PRIMARY SUCCESS CONDITION]
============================================================

The repaired program MUST:

1. Compile successfully.
2. Execute successfully.
3. Terminate normally.
4. Finish within the execution time limit.
5. Preserve the original problem intent and required concepts.
6. Produce a deterministic output suitable for an output-prediction question.

The provided code has already FAILED real validation.

Therefore:

- Do NOT assume the current code is correct.
- Do NOT ignore the validation error.
- Do NOT return the current code unchanged.
- You MUST make a substantive change that directly addresses the failure.
- Passing actual validation has higher priority than minimizing edits.


============================================================
[COMPILER ERROR REPAIR]
============================================================

If the failure stage is "compile":

- Treat the compiler error as authoritative.
- Read the exact compiler message carefully.
- Identify the exact source expression, method call, field access,
  type declaration, argument, return value, or access-control rule
  responsible for the error.
- Directly repair the actual cause of the compiler error.
- Do NOT merely modify nearby unrelated code.
- Ensure that the exact reported compiler error cannot remain afterward.

IMPORTANT:

The compiler may report only one or some occurrences of a repeated problem.

After identifying an error pattern, inspect the ENTIRE source code for
all other occurrences of the same invalid pattern.

For example, if direct access to a private field such as:

    obj.code

is invalid outside the declaring class, inspect every external use of
that private field and repair ALL invalid occurrences.

Do NOT fix only one occurrence while leaving another equivalent invalid
field access, method call, argument mismatch, or type mismatch unchanged.

After repairing the code, mentally recompile the COMPLETE modified source
from top to bottom.


============================================================
[JAVA ACCESS CONTROL]
============================================================

Respect Java access-control rules exactly.

A private field or private method may only be accessed directly inside
the class that declares it.

A subclass MUST NOT directly access a private field declared by its
superclass.

If a subclass needs access to a private field declared in a superclass,
the accessor MUST be declared inside the superclass that owns the field.

WRONG:

    class Parent {{
        private int value;
    }}

    class Child extends Parent {{
        int getValue() {{
            return value;
        }}
    }}

The getter above is invalid because Child still directly accesses
Parent.value.

CORRECT:

    class Parent {{
        private int value;

        protected int getValue() {{
            return value;
        }}
    }}

    class Child extends Parent {{
        void printValue() {{
            System.out.print(getValue());
        }}
    }}

Never create a getter inside a subclass that directly reads a private
field declared by its superclass.

If a suitable getter or method already exists in the declaring class,
use that existing accessor instead of adding unnecessary code.


============================================================
[JAVA INHERITANCE AND METHOD RULES]
============================================================

Apply Java inheritance, overriding, overloading, and static method rules
correctly.

- Do not confuse overriding with overloading.
- An overriding method must have a valid compatible signature.
- A static method is not overridden as an instance method.
- Do not declare an instance method in a subclass with the same signature
  as a static method in the superclass.
- If the intended concept is static method hiding, both methods must be static.
- super.method() may only be used when that method actually exists and is
  accessible in the superclass.
- A parent-type reference may directly call only methods available through
  its declared type.
- Preserve polymorphism when polymorphism is one of the required concepts.


============================================================
[JAVA TYPE AND METHOD SAFETY]
============================================================

Ensure that every method call is valid.

Check:

- method name,
- argument count,
- argument types,
- return type,
- receiving variable type,
- static versus instance context,
- accessibility.

Do NOT invent nonexistent methods, fields, or variables.

If a method requires no arguments, do not call it with arguments.

If a method requires arguments, provide arguments with compatible types.

If a variable declaration type is incorrect, correct the declaration type
instead of using an unjustified cast.

Do NOT use unsafe or meaningless casts merely to silence compiler errors.

If two types have no valid inheritance or implementation relationship,
do not force a cast between them.


============================================================
[JAVA OBJECT AND STRING OPERATIONS]
============================================================

Java does not allow arbitrary object + object arithmetic/string operations.

Even if toString() is overridden, this does NOT make:

    obj1 + obj2

valid when neither operand establishes String concatenation.

If multiple objects must be concatenated as text, use a valid String
operand or explicit toString() calls while preserving the intended output.


============================================================
[JAVA FUNCTIONAL INTERFACES AND GENERICS]
============================================================

Do not treat unrelated functional interface types as interchangeable.

For example, java.util.function.Function and a user-defined functional
interface are different types unless the code explicitly provides a valid
conversion.

If a method expects a particular functional interface, the lambda or
variable passed to it must have a compatible type.

For generic methods:

- infer types consistently,
- match generic return types correctly,
- store returned values in compatible variables,
- do not store a String result in an int variable,
- do not use unchecked casts simply to suppress a type error.


============================================================
[RUNTIME FAILURE REPAIR]
============================================================

If the failure stage is "run":

- The program may compile correctly but is still invalid because actual
  execution failed.
- Analyze the runtime error and stack trace.
- Identify the actual operation that caused abnormal termination.
- Repair that cause while preserving the intended concepts.
- The final program MUST terminate normally.
- No uncaught exception may escape from main.

Do not hide unrelated problems by wrapping the entire program in a
meaningless catch block.


============================================================
[EXCEPTION HANDLING]
============================================================

If exceptions are part of the required concepts, preserve meaningful
exception-handling behavior.

Intentional exceptions may be generated for the purpose of the question,
but they MUST eventually be handled so that the program terminates normally.

If an exception is thrown inside a catch block:

- trace the newly thrown exception,
- determine where it propagates,
- ensure that an appropriate outer try-catch handles it,
- preserve meaningful finally behavior,
- ensure that no exception escapes from main.

Do NOT remove exception-handling concepts merely to make the program run.

Do NOT simply delete a throw statement if exception propagation is an
important required concept.

Instead, repair the surrounding control flow so the intended exception
behavior remains observable while the program still terminates normally.


============================================================
[EXECUTION TIMEOUT REPAIR]
============================================================

If:

- the failure stage is "execution_timeout", OR
- the runtime error contains "execution_timeout",

treat the failure specifically as a termination/performance problem.

Determine whether the cause is:

1. infinite recursion,
2. an infinite loop,
3. excessive recursion depth,
4. exponential computation,
5. an unrealistically large generated input.

If recursion is one of the required concepts:

- preserve recursion,
- do NOT replace it with a constant result,
- do NOT remove the recursive algorithm merely to avoid the timeout.

If the recursive or iterative algorithm itself is valid but the generated
input is unrealistically large for a hand-traceable output-prediction exam
question, prefer reducing ONLY the input to a reasonable value.

For example, if a recursive function is conceptually valid but an input
such as 123 causes exponential computation, use a smaller input that still
requires meaningful tracing of the same recursive logic.

Do NOT change the recurrence relation unnecessarily merely to make it faster.

The repaired problem must remain non-trivial but must complete within the
execution limit.


============================================================
[REPEATED REPAIR ATTEMPTS]
============================================================

This may be a repeated repair attempt.

If so:

- The provided code may already contain changes made by a previous repair.
- The provided validation error may be a NEW error discovered after compiling
  or executing that repaired code.
- The newest compiler/runtime error is authoritative.
- Repair the CURRENT code, not an earlier imagined version.
- Do NOT repeat an ineffective previous repair.
- Do NOT undo a previous correct repair unless it caused the new failure.
- Do NOT return the current code unchanged.

If a previous repair fixed only one occurrence of an error and validation
revealed another occurrence, fix the remaining occurrence.

If the previous repair introduced a new error, repair the new error while
preserving any valid previous corrections.


============================================================
[PROBLEM-PRESERVATION RULES]
============================================================

This is code REPAIR, not problem regeneration.

Preserve the original:

- question intent,
- important code structure,
- difficulty,
- required concepts,
- meaningful execution flow.

Do NOT rewrite the entire program unless necessary.

Do NOT simplify away important concepts such as:

- inheritance,
- polymorphism,
- overriding,
- overloading,
- recursion,
- exception handling,
- lambda expressions,
- functional interfaces,
- generics,
- object references,
- arrays,
- enum behavior,
- static behavior.

Make the smallest set of changes that produces a VALID problem.

However, validation correctness has priority over minimizing edits.


============================================================
[OUTPUT-PREDICTION REQUIREMENTS]
============================================================

The final code is intended for an output-prediction problem.

Therefore:

- it MUST compile,
- it MUST run,
- it MUST terminate normally,
- it MUST finish within the time limit,
- its output MUST be deterministic,
- the output must be derivable from the source code,
- it must not depend on undefined behavior or external state.

Do NOT provide the predicted answer.

Only repair the source code.



============================================================
[CRITICAL REPAIR PATTERNS]
============================================================

1. RETHROWN EXCEPTIONS

A catch block attached to a try statement does NOT catch a new exception
thrown from another catch block of that same try statement.

WRONG:

    try {{
        // code
    }} catch (ArithmeticException e) {{
        throw new RuntimeException();
    }} catch (Exception e) {{
        // This does NOT catch the RuntimeException thrown above.
    }}

The second catch does NOT catch the RuntimeException thrown by the first
catch block.

If rethrowing is part of the required concepts, preserve the rethrow and
add an appropriate OUTER try-catch that eventually handles the rethrown
exception.

CORRECT PATTERN:

    try {{
        try {{
            // code that may throw ArithmeticException
        }} catch (ArithmeticException e) {{
            throw new RuntimeException();
        }} finally {{
            // preserve meaningful finally behavior
        }}
    }} catch (RuntimeException e) {{
        // handle the rethrown exception here
    }}

Do NOT simply remove the throw statement when exception rethrowing is a
required concept.

Do NOT return the unchanged code when an uncaught rethrown exception is
the confirmed runtime failure.


2. FUNCTIONAL INTERFACE TYPE MISMATCH

A lambda expression is instantiated according to its target functional
interface type.

An object created as one functional-interface type cannot later be converted
to an unrelated functional-interface type merely by casting.

WRONG:

    BiFunction<Integer, Integer, Integer> func = (a, b) -> a + b;
    execute((Calculator) func, 1, 2);

WRONG:

    BiFunction<Integer, Integer, Integer> func = (a, b) -> a + b;

    execute(
        (Calculator & BiFunction<Integer, Integer, Integer>) func,
        1,
        2
    );

These casts may compile in some forms but can fail at runtime with
ClassCastException because the existing lambda object was created with
BiFunction as its target type.

If execute requires Calculator, declare the lambda using Calculator as
its target type.

CORRECT:

    Calculator func = (a, b) -> a + b;
    execute(func, 1, 2);

When repairing a functional-interface mismatch:

- Prefer correcting the lambda variable's declared target type.
- Preserve the lambda expression.
- Preserve the custom functional interface if it is part of the required
  concepts.
- Do NOT use forced casts between unrelated functional interfaces.
- Do NOT use intersection casts as a workaround for an already-created
  lambda object of an incompatible target type.


3. RECURSION TIMEOUT

When the validation error contains "execution_timeout" and the program
contains recursion, inspect the input size BEFORE modifying the recursive
algorithm.

Determine whether:

- the recursion has a valid base case,
- recursive arguments move toward the base case,
- the recurrence itself is valid,
- the generated input is simply too large.

If the recursive structure is valid and recursion is one of the required
concepts, preserve the recursive algorithm.

Example:

    System.out.println(process("123"));

If process eventually performs Fibonacci-like exponential recursion,
an input such as 123 is not reasonable for a hand-traceable
output-prediction examination problem.

WRONG REPAIR:

    Keep input "123" and modify a valid recurrence relation only to try
    to make execution faster.

PREFERRED REPAIR:

    System.out.println(process("7"));

Use a small but non-trivial input such as 5, 6, 7, or 8 when appropriate
for the existing recursive logic.

When excessive generated input is the actual cause of timeout:

- Change the input size rather than the valid recurrence relation.
- Preserve recursion.
- Preserve method overloading if it is a required concept.
- Preserve the original base cases unless they are themselves incorrect.
- Keep the problem sufficiently non-trivial for manual output tracing.
- Ensure the repaired input completes comfortably within the execution
  time limit.

Only modify the recursive algorithm itself when the recurrence or
termination condition is actually incorrect.



============================================================
[FINAL SELF-CHECK]
============================================================

Before returning the repaired code, internally verify the COMPLETE source.

Check all of the following:

1. Will this exact source compile?

2. Will this exact source execute successfully?

3. Will it terminate within the time limit?

4. Can any exception escape from main?

5. Does the reported validation error still exist?

6. Did I directly modify the expression or structure responsible for
   the reported failure?

7. Did I inspect the entire source for additional occurrences of the
   same error pattern?

8. If a private field is involved, is every direct access legal?

9. If I added an accessor for a private superclass field, is that accessor
   declared in the class that actually owns the private field?

10. Are all method calls valid with respect to method name, argument count,
    argument types, return type, and accessibility?

11. Are static and instance methods used correctly?

12. Are overriding and overloading rules satisfied?

13. Are all variable and generic types compatible?

14. If recursion or loops are involved, will they terminate in practical time?

15. If exceptions are intentionally generated, are they eventually handled?

16. Did I preserve the required concepts?

17. Did I preserve the original problem intent and difficulty?

18. If this is a repeated repair, did I make a substantive correction
    instead of repeating the previously failed repair?

If ANY of these checks would fail, revise the code internally before
returning it.


============================================================
[LANGUAGE]
============================================================

{language}


============================================================
[VALIDATION FAILURE STAGE]
============================================================

{stage}


============================================================
[ACTUAL COMPILER / RUNTIME ERROR]
============================================================

{error}


============================================================
[REQUIRED CONCEPTS]
============================================================

{concepts_text}


============================================================
[ORIGINAL GENERATION PLAN]
============================================================

{plan_text}


============================================================
[CODE TO REPAIR]
============================================================

{code}


============================================================
[OUTPUT REQUIREMENT]
============================================================

Analyze the validation failure internally.

Return ONLY the complete repaired source code.

Do NOT provide explanations.
Do NOT provide the predicted answer.
Do NOT return JSON.
Do NOT return Markdown.
Do NOT use code fences.
Do NOT include any text before or after the source code.
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