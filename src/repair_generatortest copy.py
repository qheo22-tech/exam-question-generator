import json
import os
import re
import sys
from pathlib import Path

import requests


# ============================================================
# 경로 설정
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
GENERATED_DIR = BASE_DIR / "data" / "generated"
INPUT_FILE = GENERATED_DIR / "generation_failures.jsonl"
REPAIR_DIR = GENERATED_DIR / "repair"
REPAIR_DIR.mkdir(parents=True, exist_ok=True)

SUCCESS_FILE = REPAIR_DIR / "repair_success.jsonl"
FAILED_FILE = REPAIR_DIR / "repair_failed.jsonl"
ALL_RESULTS_FILE = REPAIR_DIR / "repair_results.jsonl"


# ============================================================
# Ollama 설정
# ============================================================

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11435")
REPAIR_MODEL = os.getenv("REPAIR_MODEL", "qwen3-coder-30b")
REQUEST_TIMEOUT = int(os.getenv("REPAIR_REQUEST_TIMEOUT", "300"))
MAX_REPAIR_ATTEMPTS = int(os.getenv("MAX_REPAIR_ATTEMPTS", "3"))
NUM_CTX = int(os.getenv("REPAIR_NUM_CTX", "16384"))


# ============================================================
# 기존 code executor import
# ============================================================

try:
    from generate_questions import execute_code
except ImportError as e:
    print("[ERROR] generate_questions.py에서 execute_code를 import할 수 없습니다.")
    print(e)
    sys.exit(1)


# ============================================================
# JSONL
# ============================================================

def load_jsonl(path):
    items = []
    if not path.exists():
        print(f"[ERROR] 파일을 찾을 수 없습니다: {path}")
        return items

    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[WARNING] JSON 파싱 실패 (line {line_number}): {e}")
    return items


def append_jsonl(path, data):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def reset_output_files():
    for path in [SUCCESS_FILE, FAILED_FILE, ALL_RESULTS_FILE]:
        if path.exists():
            path.unlink()


# ============================================================
# 응답 정리
# ============================================================

def clean_code_response(text):
    if text is None:
        return ""

    text = str(text).strip()

    # 앞뒤 설명이 섞였더라도 fenced code가 있으면 가장 큰 코드 블록을 사용
    blocks = re.findall(r"```(?:[A-Za-z0-9_+.#-]+)?\s*\n?(.*?)```", text, flags=re.S)
    if blocks:
        text = max(blocks, key=len).strip()

    # 남아 있는 fence 제거
    text = re.sub(r"^```(?:[A-Za-z0-9_+.#-]+)?\s*", "", text).strip()
    text = re.sub(r"\s*```$", "", text).strip()

    return text


def extract_json_object(text):
    """모델의 JSON 응답이 fence/설명과 섞여도 최대한 복구한다."""
    if not text:
        raise ValueError("빈 diagnosis 응답")

    raw = str(text).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I).strip()
    raw = re.sub(r"\s*```$", "", raw).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start:end + 1])

    raise ValueError(f"diagnosis JSON 파싱 실패: {raw[:500]}")


# ============================================================
# Repair type 분류
# ============================================================

def classify_repair_type(item):
    stage = str(item.get("stage", "")).lower()
    error = str(item.get("error", "")).lower()
    code = str(item.get("code", "")).lower()
    text = f"{stage}\n{error}"

    if "execution_timeout" in text or "timed out" in text or "timeout" in text:
        return "timeout"

    if (
        ("classcastexception" in error and any(x in (error + code) for x in ["lambda", "bifunction", "function", "calculator"]))
        or ("bifunction" in code and ("classcastexception" in error or "incompatible types" in error))
    ):
        return "functional_interface"

    if any(x in error for x in [
        "has private access", "has protected access", "private access", "protected access"
    ]):
        return "access_control"

    # javac의 overload/signature 오류를 일반 type 오류보다 먼저 잡는다.
    if (
        "cannot be applied to given types" in error
        or ("method" in error and "required:" in error and "found:" in error)
        or ("cannot find symbol" in error and "method" in error)
        or re.search(r"incompatible types:.*cannot be converted", error, flags=re.S)
    ):
        return "method_signature"

    if (
        (stage == "run" and any(x in error for x in ["exception", "error"]))
        or any(x in error for x in [
            "exception in thread", "arithmeticexception", "runtimeexception",
            "nullpointerexception", "indexoutofboundsexception", "classcastexception",
            "stackoverflowerror"
        ])
    ):
        return "exception"

    if any(x in error for x in [
        "incompatible types", "cannot be dereferenced", "cannot find symbol",
        "required:", "found:"
    ]):
        return "type_method"

    return "general"


# ============================================================
# Specialized diagnosis hints
# ============================================================

def build_specialized_hint(repair_type):
    hints = {
        "access_control": """
JAVA ACCESS CONTROL:
- private superclass fields/methods cannot be directly accessed by a subclass.
- adding super. does NOT make private access legal.
- prefer an existing accessible accessor; otherwise add/change a minimal protected/public accessor in the declaring class.
- preserve the access-control concept when it is part of the question.
""",
        "method_signature": """
JAVA METHOD SIGNATURE / OVERLOAD:
- identify the compile-time type of the receiver.
- enumerate methods actually available on that receiver.
- verify argument count and argument types.
- a method in a sibling/unrelated subclass is not available on this receiver.
- when overloading is an intended concept, prefer adding the missing overload to the correct receiver class rather than weakening the concept by changing the argument arbitrarily.
- "cannot find symbol ... location: variable X of type T" means the method is NOT DECLARED in the static type T.
  Changing access modifiers (public/protected/private) NEVER fixes this.
  Valid fixes: (a) cast the receiver: ((B) obj).modifyX(), (b) change the variable's declared type to B,
  (c) declare the method in T.
- Choose the fix that keeps the original concepts (inheritance/overriding/access control) intact.
""",
        "functional_interface": """
JAVA FUNCTIONAL INTERFACE / LAMBDA:
- a lambda object is created for its target functional-interface type.
- do not cast an existing lambda object between unrelated functional interfaces.
- prefer fixing the lambda declaration target type or the receiving method parameter relationship.
- preserve the lambda and custom functional-interface concept when required.
""",
        "exception": """
JAVA EXCEPTION FLOW:
- trace the exact Throwable from the exact throw/failing statement to main.
- a sibling catch does not catch a new exception thrown from another catch of the same try.
- Error is not a subclass of Exception.
- a handler added after an uncaught throw cannot catch that earlier throw.
- if rethrowing is intentional, use an enclosing handler that actually surrounds the propagation path.
- inspect finally: a Throwable from finally may replace one already propagating.
- do not merely delete the throw if exception flow is a required concept.
""",
        "timeout": """
TIMEOUT / RECURSION:

This is an output-prediction examination problem.
The program must finish quickly enough to be manually traceable.

FIRST determine whether the recursive algorithm itself is logically valid.

If:
- a valid base case exists,
- recursive arguments move toward the base case,
- recursion eventually terminates mathematically,
- but the concrete input is excessively large,

then the INPUT VALUE is the defect.

In that case, reduce ONLY the concrete input value.

Example:

    System.out.println(process("123"));

If process(123) performs Fibonacci-like exponential recursion,
DO NOT modify the recursive algorithm.

Prefer:

    System.out.println(process("7"));

or another small non-trivial input roughly in the range 5-10.

CRITICAL PRESERVATION RULES:

- Preserve the original recurrence relation.
- Preserve all existing recursive methods.
- Preserve method overloading.
- Preserve existing method signatures.
- Preserve existing base cases when they are logically valid.
- Preserve Integer.valueOf / string conversion when relevant.

DO NOT:

- add recursion-depth parameters,
- add artificial depth cutoffs,
- add arbitrary guards such as value > 100000,
- add memoization merely to avoid timeout,
- replace recursion with iteration,
- change a valid recurrence relation,
- return a constant merely to terminate,
- rewrite the recursive methods when reducing the input alone solves the timeout.

For Fibonacci-like or exponential recursion with an accidentally excessive
generated input, the preferred repair is to change ONLY the input in main
to a small but non-trivial value suitable for manual tracing.

Before proposing any algorithm modification, explicitly verify that reducing
the concrete input alone would NOT solve the timeout.
""",
        "type_method": """
TYPE / METHOD:
- use the compiler error as authoritative.
- check declaration type, return type, receiver type, argument types, array element types, and static/instance context.
- do not hide errors with unjustified casts.
""",
        "general": """
GENERAL:
- identify the exact failing expression/path from the validator error.
- make the smallest substantive repair.
- inspect the complete source for the same repeated pattern and for newly introduced errors.
""",
    }
    return hints.get(repair_type, hints["general"]).strip()


# ============================================================
# 1단계: Diagnosis prompt
# ============================================================

def build_diagnosis_prompt(item, attempt_history=None):
    language = item.get("language", "")
    stage = item.get("stage", "")
    error = item.get("error", "")
    code = item.get("code", "")
    concepts = item.get("concepts", [])
    generation_plan = item.get("generation_plan", {})
    repair_type = classify_repair_type(item)

    history_text = "없음"
    if attempt_history:
        recent = attempt_history[-2:]
        compact = []
        for h in recent:
            compact.append({
                "attempt": h.get("attempt"),
                "diagnosis": h.get("diagnosis"),
                "validation": h.get("validation"),
            })
        history_text = json.dumps(compact, ensure_ascii=False, indent=2)

    return f"""
You are the DIAGNOSIS stage of an automatic code-repair pipeline for Korean Engineer Information Processing practical-exam style questions.

Do NOT repair the source yet.
Do NOT output source code.
Analyze the ACTUAL validator failure and decide exactly what must change.

The compiler/runtime error is authoritative.
The next stage will receive your diagnosis and perform the edit.

REPAIR CATEGORY: {repair_type}

{build_specialized_hint(repair_type)}

MANDATORY ANALYSIS:
1. Identify the exact failing statement/expression or control-flow region.
2. Explain the language rule that makes it fail.
3. State a concrete repair operation, not vague advice.
4. State which declarations/statements must be changed.
5. State what concepts must be preserved.
6. Check whether the same error pattern occurs elsewhere in the source.
7. Predict what new error could be introduced by the proposed repair.
8. For runtime failures, trace the actual execution/exception path.
9. For compile failures, reason from compile-time types/signatures/accessibility.

Return ONLY one JSON object with exactly these keys:
{{
  "repair_type": "...",
  "error_location": "...",
  "root_cause": "...",
  "repair_strategy": "...",
  "required_edits": ["..."],
  "must_preserve": ["..."],
  "same_pattern_checks": ["..."],
  "risk_checks": ["..."],
  "reasoning_summary": "..."
}}

[LANGUAGE]
{language}

[VALIDATION STAGE]
{stage}

[ACTUAL COMPILER / RUNTIME ERROR]
{error}

[REQUIRED CONCEPTS]
{json.dumps(concepts, ensure_ascii=False)}

[ORIGINAL GENERATION PLAN]
{json.dumps(generation_plan, ensure_ascii=False, indent=2)}

[RECENT FAILED REPAIR HISTORY]
{history_text}

[CURRENT SOURCE]
{code}
""".strip()


# ============================================================
# 2단계: Repair prompt
# ============================================================

def build_repair_prompt(item, diagnosis, original_code, attempt_number):
    language = item.get("language", "")
    stage = item.get("stage", "")
    error = item.get("error", "")
    code = item.get("code", "")
    concepts = item.get("concepts", [])
    generation_plan = item.get("generation_plan", {})
    repair_type = diagnosis.get("repair_type") or classify_repair_type(item)

    return f"""
You are the REPAIR stage of an automatic code-repair pipeline.
The diagnosis stage has already analyzed the failure.
Your job is to EDIT the CURRENT SOURCE accordingly.

This source has FAILED real compilation/execution validation.
Returning unchanged source is invalid.

ATTEMPT: {attempt_number}/{MAX_REPAIR_ATTEMPTS}
REPAIR CATEGORY: {repair_type}

{build_specialized_hint(repair_type)}

[AUTHORITATIVE DIAGNOSIS]
{json.dumps(diagnosis, ensure_ascii=False, indent=2)}

[ACTUAL VALIDATION FAILURE]
stage: {stage}
error:
{error}

[REQUIRED CONCEPTS]
{json.dumps(concepts, ensure_ascii=False)}

[ORIGINAL GENERATION PLAN]
{json.dumps(generation_plan, ensure_ascii=False, indent=2)}

[CURRENT SOURCE]
{code}

MANDATORY REPAIR RULES:
- Make the concrete edits specified by the diagnosis.
- The returned source MUST be textually different from CURRENT SOURCE.
- The repair must directly affect the reported failing statement/declaration/control-flow path.
- Do not satisfy the change requirement using comments, whitespace, formatting, or unrelated renaming.
- Preserve the original problem intent and required concepts.
- Prefer the smallest substantive repair that makes the program valid.
- Do not remove a core concept merely to make validation pass.
- Inspect the COMPLETE source after the edit.
- Check all repeated occurrences of the same invalid pattern.
- Check receiver compile-time types, method signatures, access modifiers, return types, and control flow where relevant.
- For Java exception flow, ensure the ACTUAL original throw/rethrow is on the path of a compatible enclosing handler.
- No uncaught Throwable may escape main when normal execution is required.
- The final program must compile, execute, terminate normally, and finish within the time limit.
- Do not provide the predicted output answer.

ANTI-LOOP RULE:
The original source below is known to have failed. Do not reproduce it unchanged.

[ORIGINAL FAILED SOURCE]
{original_code}

OUTPUT:
Return ONLY the complete repaired source code.
No explanation. No JSON. No Markdown. No code fences.
""".strip()


# ============================================================
# Ollama 공통 호출
# ============================================================

def call_ollama(messages, *, temperature=0.0, json_mode=False):
    payload = {
        "model": REPAIR_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "num_ctx": NUM_CTX,
            "temperature": temperature,
        },
    }

    # Ollama는 JSON mode를 지원한다. 모델/버전에 따라 실패하면 caller에서 fallback.
    if json_mode:
        payload["format"] = "json"

    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return data.get("message", {}).get("content", "")


def call_diagnosis_model(item, attempt_history=None):
    prompt = build_diagnosis_prompt(item, attempt_history=attempt_history)
    messages = [
        {
            "role": "system",
            "content": (
                "You diagnose code failures precisely. "
                "Use the validator error as authoritative evidence. "
                "Return only valid JSON and do not repair the code yet."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        content = call_ollama(messages, temperature=0.0, json_mode=True)
    except requests.HTTPError:
        # 오래된 Ollama 등에서 format=json 문제가 있으면 일반 호출로 fallback
        content = call_ollama(messages, temperature=0.0, json_mode=False)

    diagnosis = extract_json_object(content)

    # 필수 필드 보정
    diagnosis.setdefault("repair_type", classify_repair_type(item))
    diagnosis.setdefault("error_location", "validator error 참조")
    diagnosis.setdefault("root_cause", str(item.get("error", "")))
    diagnosis.setdefault("repair_strategy", "validator 오류의 직접 원인을 수정")
    diagnosis.setdefault("required_edits", [])
    diagnosis.setdefault("must_preserve", item.get("concepts", []))
    diagnosis.setdefault("same_pattern_checks", [])
    diagnosis.setdefault("risk_checks", [])
    diagnosis.setdefault("reasoning_summary", "")
    return diagnosis


def call_repair_model(item, diagnosis, original_code, attempt_number):
    prompt = build_repair_prompt(
        item=item,
        diagnosis=diagnosis,
        original_code=original_code,
        attempt_number=attempt_number,
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You repair code from a prior diagnosis. "
                "Return only the complete source code. "
                "Never return the known-failing source unchanged."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    content = call_ollama(messages, temperature=0.05, json_mode=False)
    return clean_code_response(content)


# ============================================================
# validation helper
# ============================================================

def validate_code(language, code):
    try:
        return execute_code(language=language, code=code)
    except Exception as e:
        return {
            "success": False,
            "stage": "validator",
            "error": str(e),
        }


def normalize_code_for_compare(code):
    """주석/공백만 바꾼 가짜 수정을 어느 정도 걸러낸다."""
    if not code:
        return ""

    text = str(code)
    # block comments
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    # line comments (문자열 내부 //까지 완벽 파싱하려는 목적은 아님)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    # whitespace
    text = re.sub(r"\s+", "", text)
    return text


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
    print(f"초기 분류: {classify_repair_type(item)}")

    current_item = dict(item)
    current_code = original_code
    attempts = []

    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
        print()
        print("-" * 70)
        print(f"[ATTEMPT] {attempt}/{MAX_REPAIR_ATTEMPTS}")
        print("-" * 70)

        # ----------------------------------------------------
        # 1) Diagnosis
        # ----------------------------------------------------
        print("[DIAGNOSIS] 오류 원인을 먼저 분석합니다...")
        try:
            diagnosis = call_diagnosis_model(
                current_item,
                attempt_history=attempts,
            )
        except Exception as e:
            print("[DIAGNOSIS] ❌ 모델 호출/JSON 파싱 실패")
            print(e)

            attempt_data = {
                "attempt": attempt,
                "input_stage": current_item.get("stage"),
                "input_error": current_item.get("error"),
                "input_code": current_code,
                "diagnosis": None,
                "repaired_code": None,
                "validation": {
                    "success": False,
                    "stage": "diagnosis_model",
                    "error": str(e),
                },
            }
            attempts.append(attempt_data)

            if attempt == MAX_REPAIR_ATTEMPTS:
                return {
                    "repair_model": REPAIR_MODEL,
                    "repair_attempt": attempt,
                    "original": item,
                    "repaired_code": current_code,
                    "attempts": attempts,
                    "validation": attempt_data["validation"],
                }
            continue

        print(f"[DIAGNOSIS] 유형: {diagnosis.get('repair_type')}")
        print(f"[DIAGNOSIS] 위치: {diagnosis.get('error_location')}")
        print(f"[DIAGNOSIS] 원인: {diagnosis.get('root_cause')}")
        print(f"[DIAGNOSIS] 전략: {diagnosis.get('repair_strategy')}")

        # ----------------------------------------------------
        # 2) Repair
        # ----------------------------------------------------
        print("[MODEL] diagnosis를 바탕으로 코드를 수정합니다...")
        try:
            repaired_code = call_repair_model(
                item=current_item,
                diagnosis=diagnosis,
                original_code=original_code,
                attempt_number=attempt,
            )
        except Exception as e:
            print("[MODEL] ❌ repair 모델 호출 실패")
            print(e)

            attempt_data = {
                "attempt": attempt,
                "input_stage": current_item.get("stage"),
                "input_error": current_item.get("error"),
                "input_code": current_code,
                "diagnosis": diagnosis,
                "repaired_code": None,
                "validation": {
                    "success": False,
                    "stage": "repair_model",
                    "error": str(e),
                },
            }
            attempts.append(attempt_data)

            if attempt == MAX_REPAIR_ATTEMPTS:
                return {
                    "repair_model": REPAIR_MODEL,
                    "repair_attempt": attempt,
                    "original": item,
                    "repaired_code": current_code,
                    "attempts": attempts,
                    "validation": attempt_data["validation"],
                }
            continue

        # ----------------------------------------------------
        # 빈 응답
        # ----------------------------------------------------
        if not repaired_code:
            error_msg = "repair 모델이 빈 코드를 반환했습니다."
            print(f"[MODEL] ❌ {error_msg}")

            validation = {
                "success": False,
                "stage": "repair_empty",
                "error": error_msg,
            }
            attempts.append({
                "attempt": attempt,
                "input_stage": current_item.get("stage"),
                "input_error": current_item.get("error"),
                "input_code": current_code,
                "diagnosis": diagnosis,
                "repaired_code": "",
                "validation": validation,
            })

            current_item = dict(current_item)
            current_item["stage"] = "repair_empty"
            current_item["error"] = (
                f"{current_item.get('error', '')}\n\n"
                "[PREVIOUS REPAIR FAILURE]\n"
                "The repair model returned empty source. Return the complete source and perform the diagnosed edit."
            )
            continue

        # ----------------------------------------------------
        # 동일/가짜 수정 방지
        # ----------------------------------------------------
        exact_same = repaired_code.strip() == current_code.strip()
        semantic_same = (
            normalize_code_for_compare(repaired_code)
            == normalize_code_for_compare(current_code)
        )

        if exact_same or semantic_same:
            no_change_error = (
                "이전 repair가 실패한 CURRENT SOURCE를 실질적으로 수정하지 않았다. "
                "공백/주석/포맷 변경은 repair가 아니다. "
                "diagnosis.required_edits에 적힌 실패 원인 관련 선언/문장/제어흐름을 실제로 변경해야 한다."
            )
            print("[MODEL] ⚠️ 실질적으로 동일한 코드 반환")

            validation = {
                "success": False,
                "stage": "repair_no_change",
                "error": no_change_error,
            }
            attempts.append({
                "attempt": attempt,
                "input_stage": current_item.get("stage"),
                "input_error": current_item.get("error"),
                "input_code": current_code,
                "diagnosis": diagnosis,
                "repaired_code": repaired_code,
                "validation": validation,
            })

            if attempt == MAX_REPAIR_ATTEMPTS:
                return {
                    "repair_model": REPAIR_MODEL,
                    "repair_attempt": attempt,
                    "original": item,
                    "repaired_code": repaired_code,
                    "attempts": attempts,
                    "validation": validation,
                }

            # 핵심: 다음 attempt는 'no change'만 보내지 않고
            # 원래 validator 오류 + 이전 diagnosis + no-change 사실을 함께 보낸다.
            current_item = dict(current_item)
            current_item["code"] = current_code
            current_item["error"] = (
                f"{current_item.get('error', '')}\n\n"
                "[PREVIOUS REPAIR FAILURE: NO SUBSTANTIVE CHANGE]\n"
                f"{no_change_error}\n\n"
                "[PREVIOUS DIAGNOSIS]\n"
                f"{json.dumps(diagnosis, ensure_ascii=False, indent=2)}"
            )
            continue

        # ----------------------------------------------------
        # 3) 실제 compile / run validation
        # ----------------------------------------------------
        print("[VALIDATION] 실제 컴파일/실행...")
        execution_result = validate_code(language, repaired_code)

        attempt_data = {
            "attempt": attempt,
            "input_stage": current_item.get("stage"),
            "input_error": current_item.get("error"),
            "input_code": current_code,
            "diagnosis": diagnosis,
            "repaired_code": repaired_code,
            "validation": execution_result,
        }
        attempts.append(attempt_data)

        if execution_result.get("success"):
            print("[VALIDATION] ✅ Repair 성공")
            print("실제 출력:")
            print(repr(execution_result.get("output", "")))

            return {
                "repair_model": REPAIR_MODEL,
                "repair_attempt": attempt,
                "original": item,
                "repaired_code": repaired_code,
                "attempts": attempts,
                "validation": execution_result,
            }

        # ----------------------------------------------------
        # validation 실패 -> 다음 attempt에서는 새 오류를 재진단
        # ----------------------------------------------------
        print("[VALIDATION] ❌ Repair 실패")
        print("실패 단계:", execution_result.get("stage"))
        print("오류:")
        print(execution_result.get("error"))

        if attempt == MAX_REPAIR_ATTEMPTS:
            return {
                "repair_model": REPAIR_MODEL,
                "repair_attempt": attempt,
                "original": item,
                "repaired_code": repaired_code,
                "attempts": attempts,
                "validation": execution_result,
            }

        print("[FEEDBACK] 새 validator 오류를 다음 diagnosis에 전달합니다.")

        current_code = repaired_code
        current_item = dict(item)
        current_item["code"] = repaired_code
        current_item["stage"] = execution_result.get("stage", "unknown")
        current_item["error"] = (
            f"{execution_result.get('error', '알 수 없는 검증 오류')}\n\n"
            "[PREVIOUS DIAGNOSIS]\n"
            f"{json.dumps(diagnosis, ensure_ascii=False, indent=2)}\n\n"
            "The previous edit did not pass validation. Diagnose THIS NEW validator failure from the CURRENT SOURCE."
        )

    return {
        "repair_model": REPAIR_MODEL,
        "repair_attempt": MAX_REPAIR_ATTEMPTS,
        "original": item,
        "repaired_code": current_code,
        "attempts": attempts,
        "validation": {
            "success": False,
            "stage": "unknown",
            "error": "최대 repair 횟수를 초과했습니다.",
        },
    }


# ============================================================
# 통계
# ============================================================

def print_summary(total, success_count, failed_count):
    print()
    print()
    print("=" * 70)
    print("REPAIR BENCHMARK RESULT")
    print("=" * 70)
    print(f"Repair 모델 : {REPAIR_MODEL}")
    print(f"전체 문제   : {total}")
    print(f"성공        : {success_count}")
    print(f"실패        : {failed_count}")

    if total > 0:
        print(f"성공률      : {success_count / total * 100:.2f}%")

    print()
    print(f"전체 결과   : {ALL_RESULTS_FILE}")
    print(f"성공 결과   : {SUCCESS_FILE}")
    print(f"실패 결과   : {FAILED_FILE}")
    print("=" * 70)


# ============================================================
# main
# ============================================================

def main():
    print("=" * 70)
    print("CODE REPAIR BENCHMARK - DIAGNOSE -> REPAIR -> VALIDATE")
    print("=" * 70)
    print(f"입력 파일 : {INPUT_FILE}")
    print(f"Repair 모델: {REPAIR_MODEL}")
    print(f"Ollama URL : {OLLAMA_URL}")
    print(f"num_ctx    : {NUM_CTX}")
    print()

    failures = load_jsonl(INPUT_FILE)

    # 최초 generation 실패만 사용
    failures = [
        item for item in failures
        if item.get("failure_type") == "generation"
    ]

    if not failures:
        print("[ERROR] Repair할 실패 문제가 없습니다.")
        return

    print(f"로드된 실패 문제: {len(failures)}개")

    raw_count = input(
        f"\nRepair할 문제 수 (Enter = 전체 {len(failures)}개): "
    ).strip()

    if raw_count:
        try:
            requested_count = int(raw_count)
            if requested_count <= 0:
                raise ValueError
            failures = failures[:requested_count]
        except ValueError:
            print("[ERROR] 문제 수는 1 이상의 정수여야 합니다.")
            return

    total = len(failures)
    reset_output_files()

    success_count = 0
    failed_count = 0

    for index, item in enumerate(failures, start=1):
        result = repair_one(
            item=item,
            index=index,
            total=total,
        )

        append_jsonl(ALL_RESULTS_FILE, result)
        validation = result.get("validation", {})

        if validation.get("success"):
            success_count += 1
            append_jsonl(SUCCESS_FILE, result)
        else:
            failed_count += 1
            append_jsonl(FAILED_FILE, result)

    print_summary(
        total=total,
        success_count=success_count,
        failed_count=failed_count,
    )


if __name__ == "__main__":
    main()
