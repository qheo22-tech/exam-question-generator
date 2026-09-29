import os
import re
import sys
import subprocess
import tempfile


# ============================================================
# 출력 정규화
# ============================================================

def normalize_output(value):
    if value is None:
        return ""

    text = str(value)

    # Windows / Linux 줄바꿈 차이 통일
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # 마지막 개행/공백 제거
    return text.strip()


# ============================================================
# C
# ============================================================

def run_c(code):
    with tempfile.TemporaryDirectory() as temp_dir:

        source_path = os.path.join(
            temp_dir,
            "problem.c"
        )

        executable_path = os.path.join(
            temp_dir,
            "problem.exe"
            if os.name == "nt"
            else "problem"
        )

        with open(
            source_path,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(code)

        # 컴파일
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                source_path,
                "-o",
                executable_path
            ],
            capture_output=True,
            text=True,
            timeout=10
        )

        if compile_result.returncode != 0:
            return {
                "success": False,
                "stage": "compile",
                "error": compile_result.stderr.strip()
            }

        # 실행
        try:
            run_result = subprocess.run(
                [executable_path],
                capture_output=True,
                text=True,
                timeout=15
            )

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stage": "run",
                "error": "execution_timeout"
            }

        if run_result.returncode != 0:
            return {
                "success": False,
                "stage": "run",
                "error": run_result.stderr.strip(),
                "returncode": run_result.returncode
            }

        return {
            "success": True,
            "output": normalize_output(
                run_result.stdout
            )
        }


# ============================================================
# Java
# ============================================================

def run_java(code):

    # public class 이름 찾기
    match = re.search(
        r"public\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
        code
    )

    if match:
        class_name = match.group(1)
    else:
        # 정보처리기사 문제에서 일반적으로 Main 사용
        class_name = "Main"

    with tempfile.TemporaryDirectory() as temp_dir:

        source_path = os.path.join(
            temp_dir,
            f"{class_name}.java"
        )

        with open(
            source_path,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(code)

        # 컴파일
        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                source_path
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10
        )

        if compile_result.returncode != 0:
            return {
                "success": False,
                "stage": "compile",
                "error": compile_result.stderr.strip()
            }

        # 실행
        try:
            run_result = subprocess.run(
                [
                    "java",
                    "-Dfile.encoding=UTF-8",
                    "-cp",
                    temp_dir,
                    class_name
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15
            )

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stage": "run",
                "error": "execution_timeout"
            }

        if run_result.returncode != 0:
            return {
                "success": False,
                "stage": "run",
                "error": run_result.stderr.strip(),
                "returncode": run_result.returncode
            }

        return {
            "success": True,
            "output": normalize_output(
                run_result.stdout
            )
        }


# ============================================================
# Python
# ============================================================

def run_python(code):

    with tempfile.TemporaryDirectory() as temp_dir:

        source_path = os.path.join(
            temp_dir,
            "problem.py"
        )

        with open(
            source_path,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(code)

        try:
            run_result = subprocess.run(
                [
                    sys.executable,
                    source_path
                ],
                capture_output=True,
                text=True,
                timeout=15
            )

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "stage": "run",
                "error": "execution_timeout"
            }

        if run_result.returncode != 0:
            return {
                "success": False,
                "stage": "run",
                "error": run_result.stderr.strip(),
                "returncode": run_result.returncode
            }

        return {
            "success": True,
            "output": normalize_output(
                run_result.stdout
            )
        }


# ============================================================
# 공통 코드 실행
# ============================================================

def execute_code(language, code):

    language = str(language).strip().lower()

    if language == "c":
        return run_c(code)

    if language == "java":
        return run_java(code)

    if language in ("python", "py"):
        return run_python(code)

    return {
        "success": False,
        "stage": "language",
        "error": f"지원하지 않는 언어: {language}"
    }


# ============================================================
# 정답 검증
# ============================================================

def validate_code_answer(language, code, answer):

    try:
        result = execute_code(
            language=language,
            code=code
        )

    except FileNotFoundError as e:
        return {
            "valid": False,
            "stage": "environment",
            "error": (
                f"컴파일러 또는 실행 환경을 "
                f"찾을 수 없습니다: {e}"
            )
        }

    except subprocess.TimeoutExpired:
        return {
            "valid": False,
            "stage": "timeout",
            "error": "실행 시간이 초과되었습니다."
        }

    if not result["success"]:
        return {
            "valid": False,
            **result
        }

    actual = normalize_output(
        result["output"]
    )

    expected = normalize_output(
        answer
    )

    if actual != expected:
        return {
            "valid": False,
            "stage": "answer",
            "expected": expected,
            "actual": actual,
            "error": "실제 출력과 생성된 정답이 다릅니다."
        }

    return {
        "valid": True,
        "stage": "complete",
        "expected": expected,
        "actual": actual
    }