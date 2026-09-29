import requests
import json
import random


from pathlib import Path
from code_validator import execute_code
from prompts import build_generation_prompt






# ============================================================

# 설정

# ============================================================



OLLAMA_BASE_URL = "http://127.0.0.1:11435"

CHAT_MODEL = "qwen3-30b-a3b-2507-q5:latest"
BASE_DIR = Path(__file__).resolve().parent.parent





# ============================================================

# 데이터 경로

# ============================================================



EXAMPLES_DIR = (

    BASE_DIR

    / "data"

    / "examples"

)

GENERATED_DIR = (
    BASE_DIR
    / "data"
    / "generated"
)


def get_language_output_paths(language):
    language = normalize_text(language)

    language_dir = (
        GENERATED_DIR
        / language
    )

    generated_file = (
        language_dir
        / "generated_questions.json"
    )

    failure_log_file = (
        language_dir
        / "generation_failures.jsonl"
    )

    return (
        language_dir,
        generated_file,
        failure_log_file
    )




# ============================================================

# 지원 언어

# ============================================================



SUPPORTED_LANGUAGES = [

    "c",

    "java",

    "python"

]





# ============================================================

# 문제 데이터 로드

# ============================================================



def load_questions():



    if not EXAMPLES_DIR.exists():



        raise FileNotFoundError(

            "기출 데이터 폴더를 찾을 수 없습니다.\n"

            f"경로: {EXAMPLES_DIR}"

        )



    json_files = sorted(

        EXAMPLES_DIR.glob("*.json")

    )



    if not json_files:



        raise FileNotFoundError(

            "기출 JSON 파일을 찾을 수 없습니다.\n"

            f"경로: {EXAMPLES_DIR}"

        )



    questions = []



    for file_path in json_files:



        with open(

            file_path,

            "r",

            encoding="utf-8"

        ) as f:



            data = json.load(f)



        if not isinstance(data, list):



            raise ValueError(

                f"{file_path.name}의 최상위 구조는 "

                "배열(list)이어야 합니다."

            )



        questions.extend(data)



        print(

            f"[LOAD] {file_path.name}: "

            f"{len(data)}문제"

        )



    return questions





# ============================================================

# 개념 정규화

# ============================================================



def normalize_text(value):



    if value is None:

        return ""



    return str(value).strip().lower()





def normalize_concepts(concepts):



    return {

        normalize_text(concept)

        for concept in concepts

        if concept

    }





# ============================================================

# 언어 선택

# ============================================================



def select_language():



    print("\n[생성할 프로그래밍 언어 선택]")

    print("1. C")

    print("2. Java")

    print("3. Python")

    print("4. 랜덤")



    while True:



        choice = input(

            "\n번호를 입력하세요: "

        ).strip()



        if choice == "1":

            return "c"



        elif choice == "2":

            return "java"



        elif choice == "3":

            return "python"



        elif choice == "4":

            return random.choice(

                SUPPORTED_LANGUAGES

            )



        else:

            print(

                "1~4 중 하나를 입력해주세요."

            )





def select_seed_question(

    questions,

    language

):

    """

    선택된 언어의 실제 code_output 기출 중

    하나를 랜덤으로 선택하여 이번 문제의

    출제 개념 seed로 사용한다.

    """



    candidates = []



    for question in questions:



        if (

            question.get("type")

            != "code_output"

        ):

            continue



        if (

            normalize_text(

                question.get("language")

            )

            != normalize_text(language)

        ):

            continue



        concepts = question.get(

            "concepts",

            []

        )



        if not concepts:

            continue



        candidates.append(

            question

        )



    if not candidates:

        raise ValueError(

            f"{language} 언어의 "

            "code_output 기출을 찾을 수 없습니다."

        )



    seed = random.choice(

        candidates

    )



    return seed





# ============================================================

# 예시 문제 점수 계산

# ============================================================



def calculate_example_score(

    question,

    plan

):



    score = 0



    question_type = plan["type"]

    subject = plan.get("subject")

    language = plan.get("language")

    concepts = plan.get(

        "concepts",

        []

    )



    # --------------------------------------------------------

    # TYPE

    # --------------------------------------------------------



    if question.get("type") != question_type:

        return None



    score += 5



    # --------------------------------------------------------

    # SUBJECT

    # --------------------------------------------------------



    if (

        subject

        and normalize_text(

            question.get("subject")

        )

        == normalize_text(subject)

    ):



        score += 3



    # --------------------------------------------------------

    # LANGUAGE

    # --------------------------------------------------------



    q_language = question.get(

        "language"

    )



    if language:



        if (

            normalize_text(q_language)

            == normalize_text(language)

        ):



            score += 5



        else:



            if (

                question_type

                == "code_output"

            ):



                return None



    # --------------------------------------------------------

    # CONCEPT

    # --------------------------------------------------------



    target_concepts = (

        normalize_concepts(

            concepts

        )

    )



    question_concepts = (

        normalize_concepts(

            question.get(

                "concepts",

                []

            )

        )

    )



    matched_concepts = (

        target_concepts

        & question_concepts

    )



    score += (

        len(matched_concepts)

        * 3

    )



    return score





# ============================================================

# 예시 문제 선택

# ============================================================



def select_examples(

    questions,

    plan,

    count=3

):



    scored = []



    for question in questions:



        score = (

            calculate_example_score(

                question,

                plan

            )

        )



        if score is None:

            continue



        scored.append(

            (

                score,

                question

            )

        )



    if not scored:



        raise ValueError(

            "생성 조건에 해당하는 "

            "기출문제를 찾을 수 없습니다.\n"

            f"plan={plan}"

        )



    scored.sort(

        key=lambda item: item[0],

        reverse=True

    )



    # 가장 관련도가 높은 기출 1개는 반드시 포함

    best = scored[0]



    # 나머지는 관련도가 높은 상위 후보들에서 랜덤 선택

    remaining_pool = scored[

        1:min(len(scored), 8)

    ]



    selected = [best]



    if remaining_pool:



        selected.extend(

            random.sample(

                remaining_pool,

                min(

                    count - 1,

                    len(remaining_pool)

                )

            )

        )



    selected.sort(

        key=lambda item: item[0],

        reverse=True

    )



    return [

        question

        for score, question

        in selected

    ]





# ============================================================

# 예시 문제 → 프롬프트 문자열

# ============================================================



def build_examples_text(

    examples

):



    parts = []



    for index, question in enumerate(

        examples,

        start=1

    ):



        concepts = ", ".join(

            question.get(

                "concepts",

                []

            )

        )



        part = f"""

[기출 예시 {index}]



ID:

{question.get("id", "")}



유형:

{question.get("type", "")}



과목:

{question.get("subject", "")}



언어:

{question.get("language") or "해당 없음"}



관련 개념:

{concepts}



문제:

{question.get("question", "")}

""".strip()



        code = question.get(

            "code"

        )



        if code:



            part += f"""



코드:

{code}

"""



        original_answer = (

            question.get(

                "original_answer"

            )

        )



        if (

            original_answer

            is not None

        ):



            answer_text = json.dumps(

                original_answer,

                ensure_ascii=False

            )



            part += f"""



기출 정답:

{answer_text}

"""



        parts.append(

            part.strip()

        )



    return (

        "\n\n"

        "============================================================"

        "\n\n"

    ).join(parts)





# ============================================================

# Ollama / Qwen 호출

# ============================================================



def ask_llm(

    prompt: str

) -> str:



    response = requests.post(



        f"{OLLAMA_BASE_URL}/api/chat",



        json={



            "model": CHAT_MODEL,



            "messages": [



                {

                    "role": "system",



                    "content": (

                        "너는 정보처리기사 실기 시험의 "

                        "기출 경향을 분석하여 새로운 "

                        "연습문제를 생성하는 AI다. "

                        "사용자가 요구한 JSON 형식을 "

                        "정확하게 지켜라. "

                        "코드 출력 문제의 정답은 "

                        "생성하지 않아도 된다. "

                        "정답은 생성된 코드를 실제로 "

                        "실행하여 시스템에서 결정한다."

                    )

                },



                {

                    "role": "user",

                    "content": prompt

                }

            ],



            "stream": False,



            "keep_alive": -1,



            "format": "json",



            "options": {



                "temperature": 0.7,



                "top_p": 0.9

            }

        },



        timeout=600

    )



    if response.status_code != 200:



        print(

            "Ollama status:",

            response.status_code

        )



        print(

            "Ollama error:",

            response.text

        )



        raise RuntimeError(

            "Ollama API 호출 실패"

        )



    data = response.json()



    return (

        data["message"]["content"]

        .strip()

    )





# ============================================================

# LLM JSON 파싱

# ============================================================



def parse_llm_json(

    raw_text

):



    text = raw_text.strip()



    if text.startswith("```"):



        lines = text.splitlines()



        if lines:

            lines = lines[1:]



        if (

            lines

            and

            lines[-1].strip()

            == "```"

        ):



            lines = lines[:-1]



        text = (

            "\n".join(lines)

            .strip()

        )



    try:



        data = json.loads(

            text

        )



    except json.JSONDecodeError as e:



        print(

            "\n[LLM 원본 응답]"

        )



        print(

            raw_text

        )



        raise ValueError(

            "Qwen이 올바른 JSON을 "

            "반환하지 않았습니다."

        ) from e



    if not isinstance(

        data,

        dict

    ):



        raise ValueError(

            "생성 결과의 최상위 구조는 "

            "JSON 객체여야 합니다."

        )



    return data





# ============================================================

# 생성 결과 기본 검증

# ============================================================



def validate_generated_structure(

    generated,

    plan

):



    # answer는 여기서 요구하지 않는다.

    # 실제 실행 결과로 나중에 생성한다.



    required_fields = [

        "type",

        "subject",

        "language",

        "question",

        "code",

        "concepts"

    ]



    missing = [



        field



        for field

        in required_fields



        if field

        not in generated

    ]



    if missing:



        raise ValueError(

            "생성 문제에 필수 필드가 "

            f"없습니다: {missing}"

        )



    # --------------------------------------------------------

    # TYPE

    # --------------------------------------------------------



    if (

        generated["type"]

        != plan["type"]

    ):



        raise ValueError(

            "생성된 문제 type이 "

            "계획과 다릅니다.\n"

            f"plan: {plan['type']}\n"

            f"generated: "

            f"{generated['type']}"

        )



    # --------------------------------------------------------

    # code_output

    # --------------------------------------------------------



    if (

        plan["type"]

        == "code_output"

    ):



        if not generated.get(

            "code"

        ):



            raise ValueError(

                "code_output 문제인데 "

                "code가 없습니다."

            )



        expected_language = (

            normalize_text(

                plan.get(

                    "language"

                )

            )

        )



        generated_language = (

            normalize_text(

                generated.get(

                    "language"

                )

            )

        )



        if (

            expected_language

            != generated_language

        ):



            raise ValueError(

                "생성된 코드 언어가 "

                "계획과 다릅니다.\n"

                f"plan: "

                f"{expected_language}\n"

                f"generated: "

                f"{generated_language}"

            )



    # --------------------------------------------------------

    # concepts

    # --------------------------------------------------------



    if not isinstance(

        generated.get(

            "concepts"

        ),

        list

    ):



        raise ValueError(

            "concepts는 "

            "배열이어야 합니다."

        )



    return True





# ============================================================

# 생성 문제 메타데이터 추가

# ============================================================



def add_generation_metadata(

    generated,

    plan,

    examples

):



    generated[

        "source"

    ] = "generated"



    generated[

        "generated_from"

    ] = [



        q.get("id")



        for q in examples



        if q.get("id")

    ]



    generated[

        "generation_plan"

    ] = {



        "type":

            plan.get("type"),



        "subject":

            plan.get("subject"),



        "language":

            plan.get("language"),



        "concepts":

            plan.get(

                "concepts",

                []

            )

    }



    return generated





# ============================================================

# 실행 실패 출력

# ============================================================



def print_execution_failure(

    result

):



    print(

        "\n[EXECUTION] ❌ "

        "코드 실행 검증 실패"

    )



    print(

        "실패 단계:",

        result.get(

            "stage"

        )

    )



    error = result.get(

        "error"

    )



    if error:



        print(

            "오류:"

        )



        print(

            error

        )





def save_generation_failure(

    generated,

    plan,

    examples,

    result,

    attempt,

    failure_type="generation"

):

    language = normalize_text(
        plan.get("language")
    )

    (
        language_dir,
        _,
        failure_log_file
    ) = get_language_output_paths(language)

    language_dir.mkdir(
        parents=True,
        exist_ok=True
    )



    failure_data = {

        "failure_type": failure_type,

        "language": plan.get("language"),

        "attempt": attempt,

        "stage": result.get("stage"),

        "error": result.get("error"),

        "code": (

            generated.get("code")

            if isinstance(generated, dict)

            else None

        ),

        "question": (

            generated.get("question")

            if isinstance(generated, dict)

            else None

        ),

        "concepts": (

            generated.get("concepts", [])

            if isinstance(generated, dict)

            else []

        ),

        "generation_plan": plan,

        "generated_from": [

            q.get("id")

            for q in examples

            if q.get("id")

        ]

    }



    if result.get("returncode") is not None:

        failure_data["returncode"] = (

            result.get("returncode")

        )



    with open(
        failure_log_file,
        "a",
        encoding="utf-8"
    ) as f:

        f.write(

            json.dumps(

                failure_data,

                ensure_ascii=False

            )

            + "\n"

        )



    print(
        f"[FAILURE LOG] 저장: "
        f"{failure_log_file}"
    )


def build_code_safety_rules(language):
    language = normalize_text(language)

    common_rules = """
[코드 생성 필수 검증 규칙]

- 생성된 코드는 반드시 실제 컴파일에 성공해야 한다.
- 문법 오류, 타입 오류, 접근 오류, 존재하지 않는 함수/메서드 호출 등
  어떠한 컴파일 오류도 포함하지 마라.
- 컴파일 오류 자체를 찾게 하는 문제를 생성하지 마라.

- 출력 결과를 묻는 문제이므로 프로그램은 반드시 정상 실행되고 정상 종료되어야 한다.
- 처리되지 않은 예외로 프로그램이 비정상 종료되지 않게 하라.
- 무한 루프, 과도한 재귀 등으로 실행 시간이 초과되지 않게 하라.

- 예외 처리 문제에서 의도적으로 예외를 발생시키는 것은 가능하다.
- 단, 발생한 예외는 코드 내부에서 최종적으로 처리되어야 한다.
- catch에서 새로운 예외를 다시 throw하는 경우에도
  바깥 try-catch 등에서 최종 처리하여 프로그램이 정상 종료되게 하라.
- finally 실행 후 처리되지 않은 예외가 외부로 전파되는 코드를 만들지 마라.

- 문제의 난도를 낮추기 위해 핵심 로직이나 출제 개념을 제거하거나 단순화하지 마라.
- 여러 개념을 결합한 문제도 정상적인 코드 범위에서 생성할 수 있다.

- 호출하는 함수/메서드의 이름, 매개변수 개수와 타입을 실제 선언과 일치시켜라.
- 참조 변수의 정적 타입에서 호출 가능한 메서드인지 확인하라.
- 존재하지 않는 함수, 메서드, 필드, 변수를 사용하지 마라.

- 최종 출력값이 명확하게 결정되는 코드만 생성하라.
- answer는 작성하지 마라.
- 정답은 생성 후 실제 컴파일 및 실행 결과로 결정한다.
""".strip()

    if language == "java":
        language_rules = """
[Java 코드 검증 규칙]

- private 필드와 private 메서드는 선언한 클래스 외부에서 직접 접근하지 마라.
- 자식 클래스에서 부모 클래스의 private 멤버를 직접 접근하지 마라.
- super를 이용해서도 부모 클래스의 private 멤버에 접근하지 마라.
- 부모 클래스의 상태가 필요하면 접근 가능한 getter 또는 메서드를 사용하라.

- 오버라이딩과 오버로딩을 혼동하지 마라.
- 부모 타입 참조 변수에서 자식 클래스에만 존재하는 메서드를 직접 호출하지 마라.
- super.method()를 사용할 때 부모 클래스에 해당 메서드가 실제 존재하는지 확인하라.
- @Override 메서드의 시그니처가 부모 메서드와 정확히 일치하는지 확인하라.

- Java에서 객체 + 객체 연산을 사용하지 마라.
- 여러 객체를 문자열로 연결하려면 첫 피연산자를 문자열로 만들거나
  각 객체의 toString() 결과를 연결하라.
- toString()을 오버라이딩했더라도 obj1 + obj2는 허용되지 않는다.

- java.util.function.Function과 사용자 정의 함수형 인터페이스를
  서로 자동 변환 가능한 타입으로 취급하지 마라.
- 메서드가 특정 함수형 인터페이스를 요구하면
  전달하는 람다 변수도 호환되는 인터페이스 타입으로 선언하라.
- 서로 관련 없는 함수형 인터페이스를 강제 형변환하지 마라.

- 제네릭 메서드의 반환 타입과 결과를 저장하는 변수 타입을 일치시켜라.
- String 반환값을 int 변수에 저장하지 마라.
- Integer와 String 등 서로 다른 반환 타입을 정확히 구분하라.

- catch에서 다시 예외를 발생시키는 경우에도
  최종적으로 처리하여 main이 정상 종료되게 하라.

- 재귀 함수는 실제 실행 시간 내에 종료될 수 있는 입력 크기를 사용하라.
- 출력 추적 문제에서 지나치게 큰 재귀 입력값을 사용하지 마라.
""".strip()

    elif language == "c":
        language_rules = """
[C 코드 검증 규칙]

- 선언되지 않은 함수나 변수를 사용하지 마라.
- 함수 호출의 매개변수 개수와 타입을 선언과 일치시켜라.
- 배열 범위를 벗어나지 마라.
- NULL 포인터를 역참조하지 마라.
- 0으로 나누어 프로그램이 비정상 종료되지 않게 하라.
- 정의되지 않은 동작에 출력 결과가 의존하지 않게 하라.
""".strip()

    elif language == "python":
        language_rules = """
[Python 코드 검증 규칙]

- 정의되지 않은 이름을 사용하지 마라.
- 함수 호출의 인자 개수를 함수 정의와 일치시켜라.
- 처리되지 않은 예외로 프로그램이 종료되지 않게 하라.
- 출력 결과가 외부 환경이나 비결정적 동작에 의존하지 않게 하라.
""".strip()

    else:
        language_rules = ""

    if language_rules:
        return common_rules + "\n\n" + language_rules

    return common_rules


def repair_generated_code(
    generated,
    execution_result,
    plan
):
    language = normalize_text(plan.get("language"))
    safety_rules = build_code_safety_rules(language)

    repair_prompt = f"""
너는 정보처리기사 실기 코드 출력 문제의 오류를 수정하는 검증 전문가다.

아래 문제는 출제 의도와 개념 조합은 유지해야 하지만,
실제 컴파일 또는 실행 검증에 실패했다.

[최우선 목표]

1. 새로운 문제를 만들지 마라.
2. 기존 문제의 출제 개념, 난이도, 핵심 제어 흐름을 유지하라.
3. 오류 원인만 최소한으로 수정하라.
4. 수정 후 코드는 반드시 컴파일/실행에 성공하고 정상 종료되어야 한다.
5. 최종 출력값이 반드시 존재해야 한다.
6. answer는 작성하지 마라.
7. JSON 객체만 출력하라.

[수정 금지]

- 문제를 쉽게 만들기 위해 핵심 로직을 제거하지 마라.
- 기존 concepts에 포함된 핵심 개념을 삭제하지 마라.
- 여러 개념이 결합된 문제를 단순한 단일 개념 문제로 바꾸지 마라.
- 오류와 관계없는 클래스, 메서드, 반복문, 조건문, 재귀, 상속 구조를 삭제하지 마라.
- 컴파일을 통과시키기 위해 문제 전체를 다른 코드로 재작성하지 마라.
- 기존 문제와 무관한 새 클래스/새 로직을 대규모로 추가하지 마라.

[오류 수정 원칙]

- 컴파일 오류이면 오류 메시지에 나온 심볼, 접근 제어, 타입, 시그니처 문제를 먼저 해결하라.
- 실행 오류이면 stack trace의 최초 원인을 해결하라.
- 처리되지 않은 예외가 원인이면 예외 처리 개념은 유지하면서 최종적으로 main 내부에서 처리되게 하라.
- 수정은 가능한 한 적은 줄에서 수행하라.
- 단순히 오류가 난 줄을 삭제하지 말고 그 줄의 출제 의도를 보존하는 방식으로 수정하라.
- 컴파일 오류를 없애기 위한 강제 형변환은 금지한다.
- 두 타입 사이에 실제 상속/구현 관계가 없다면 캐스팅으로 해결하지 말고,
  변수의 선언 타입 또는 메서드의 매개변수 타입을 기존 출제 의도에 맞게 수정하라.
  - stage가 "run"이고 오류가 처리되지 않은 예외(unhandled/uncaught exception)라면,
  코드가 Java 문법상 올바르더라도 수정 대상이다.
- 출력값 문제는 main이 정상 종료되어야 하므로,
  main 밖으로 전파되는 예외를 최종적으로 처리하도록 최소 수정하라.
- 예외 처리, finally, 다중 catch 등의 기존 출제 개념은 유지하라.
- 단순히 throw 문을 삭제하여 문제를 쉽게 만들지 말고,
  필요한 경우 바깥쪽 try-catch를 추가하여 재던져진 예외를 최종 처리하라.

{safety_rules}

[Java repair 세부 규칙]

- private 멤버는 자식 클래스에서 직접 접근할 수 없으며 super로도 접근할 수 없다.
- private 접근 오류를 해결할 때 우선순위:
  1) 이미 존재하는 public/protected getter 또는 메서드를 사용한다.
  2) 없다면 부모 클래스에 최소한의 protected/public getter를 추가하고 자식에서 호출한다.
  3) 접근 제어자 자체가 출제 핵심이 아니고 getter 추가가 문제를 불필요하게 복잡하게 만들 때만
     private을 protected로 최소 변경한다.
- 존재하지 않는 super.getX(), super.getY() 같은 메서드를 임의로 만들어 호출하지 마라.
- 부모 타입 참조 변수는 자식 클래스에만 선언된 오버로드를 직접 호출할 수 없다.
  필요하다면 명시적 형변환 등 기존 다형성/오버로딩 의도를 유지하는 최소 수정만 하라.
- catch 내부에서 throw한 예외가 main 밖으로 빠져나가면 안 된다.
  예외 전파가 개념이라면 바깥 try-catch에서 최종 처리되게 하라.

[수정 후 내부 점검 - 반드시 수행]

1. 모든 클래스/메서드/필드/변수가 실제로 선언되어 있는가?
2. 모든 메서드 호출의 인자 개수와 타입이 선언과 일치하는가?
3. 참조 변수의 정적 타입에서 해당 메서드 호출이 가능한가?
4. private 멤버를 다른 클래스나 자식 클래스에서 직접 접근하지 않는가?
5. super를 통해 private 멤버를 접근하지 않는가?
6. @Override 시그니처가 부모 메서드와 일치하는가?
7. 처리되지 않은 예외가 main 밖으로 전파되지 않는가?
8. 무한 루프나 무한 재귀 가능성이 없는가?
9. 최소 한 번 이상 출력이 발생하는가?
10. 기존 concepts의 핵심 개념이 수정 후에도 실제 코드에 남아 있는가?
11. 오류 메시지에 여러 개의 컴파일 오류가 있다면 일부만 수정하지 말고
    오류 메시지에 포함된 모든 컴파일 오류를 각각 해결했는지 확인하라.

12. 타입 오류를 강제 형변환으로 숨기지 마라.
    서로 구현 관계가 없는 인터페이스 타입을 캐스팅하지 마라.
    예: BiFunction 객체를 사용자 정의 Operation 인터페이스로
    (Operation) obj 형태로 강제 형변환하지 마라.

13. 부모 타입 참조 변수로 자식 클래스에만 존재하는 메서드를 호출해야 한다면
    해당 객체가 실제 자식 타입임이 보장되는 경우에만
    ((Child) ref).method() 형태의 적절한 다운캐스팅을 사용하라.

14. 재귀 문제에서는 입력값과 재귀 호출 횟수를 검토하라.
    지수 시간 재귀에 지나치게 큰 입력값을 사용하지 마라.
    출력 추적 문제로 사람이 계산 가능한 크기의 입력값을 사용하라.

15. 수정한 코드 전체를 처음부터 다시 검토하라.
    기존 오류 하나를 고치는 과정에서 새로운 컴파일 오류나
    런타임 오류를 만들지 마라.

[생성 계획]

{json.dumps(plan, ensure_ascii=False, indent=2)}

[검증 실패 단계]

{execution_result.get("stage")}

[컴파일/실행 오류]

{execution_result.get("error")}

[기존 생성 문제]

{json.dumps(generated, ensure_ascii=False, indent=2)}

다음 구조의 JSON 객체만 출력하라.

{{
    "type": "code_output",
    "subject": "프로그래밍",
    "language": "{plan.get('language')}",
    "question": "...",
    "code": "...",
    "concepts": [...]
}}
"""

    raw_result = ask_llm(repair_prompt)
    repaired = parse_llm_json(raw_result)
    repaired.pop("answer", None)

    validate_generated_structure(
        repaired,
        plan
    )

    return repaired


# ============================================================

# 문제 생성

# ============================================================



def generate_question(

    questions,

    plan,

    example_count=3,

    max_attempts=5

):



    # --------------------------------------------------------

    # 참고 기출 선택

    # --------------------------------------------------------





    examples = select_examples(

        questions=questions,

        plan=plan,

        count=example_count

    )



    print(

        "\n[선택된 참고 기출]"

    )



    for question in examples:





        print(

            "-",

            question.get(

                "id"

            ),

            "/",

            ", ".join(

                question.get(

                    "concepts",

                    []

                )

            )

        )



    # --------------------------------------------------------

    # 프롬프트 생성

    # --------------------------------------------------------



    examples_text = (

        build_examples_text(

            examples

        )

    )



    prompt = (

        build_generation_prompt(
            plan=plan,
            examples_text=examples_text

        )

    )

    
    # 이 부분은 그대로
    prompt += (
        "\n\n"
        + build_code_safety_rules(
            plan.get("language")
        )
    )

# ========================================================

    # 생성 + 실행 검증

    # ========================================================



    for attempt in range(

        1,

        max_attempts + 1

    ):

        was_repaired = False   # ← 여기에 추가


        print(

            "\n======================================"

        )



        print(

            f" 문제 생성 시도 "

            f"{attempt}/{max_attempts}"

        )

        print(

            "======================================"

        )



        try:



            # ------------------------------------------------

            # Qwen 호출

            # ------------------------------------------------



            raw_result = ask_llm(

                prompt

            )



            # ------------------------------------------------

            # JSON 파싱

            # ------------------------------------------------



            generated = (

                parse_llm_json(

                    raw_result

                )

            )



            # Qwen이 answer를 보내더라도

            # 사용하지 않는다.

            generated.pop(

                "answer",

                None

            )



            # ------------------------------------------------

            # 기본 구조 검증

            # ------------------------------------------------



            validate_generated_structure(

                generated,

                plan

            )



            # ------------------------------------------------

            # 코드 실제 실행

            # ------------------------------------------------



            if (

                generated.get(

                    "type"

                )

                == "code_output"

            ):



                language = (

                    normalize_text(

                        generated.get(

                            "language"

                        )

                    )

                )



                print(

                    "\n[CODE EXECUTION]"

                )



                print(

                    "언어:",

                    language

                )



                print(

                    "생성된 코드를 "

                    "실제로 실행합니다..."

                )



                result = execute_code(

                    language=language,

                    code=generated[

                        "code"

                    ]

                )



                # --------------------------------------------

                # 컴파일 또는 실행 실패

                # --------------------------------------------



                if not result.get("success"):



                    print_execution_failure(

                        result

                    )



                    # 최초 실패 문제 기록

                    save_generation_failure(

                        generated=generated,

                        plan=plan,

                        examples=examples,

                        result=result,

                        attempt=attempt,

                        failure_type="generation"

                    )



                    print(

                        "\n[REPAIR] 기존 문제를 1회 수정합니다."

                    )



                    try:



                        repaired = repair_generated_code(

                            generated=generated,

                            execution_result=result,

                            plan=plan

                        )
                        original_code = (
                            generated.get("code", "").strip()
                        )
                        repaired_code = (
                            repaired.get("code", "").strip()
                        )

                        # 수리 모델이 코드를 전혀 바꾸지 않았다면
                        # 같은 코드를 다시 실행하지 않고 즉시 실패 처리
                        if repaired_code == original_code:
                            repair_result = {
                                "success": False,
                                "stage": "repair_no_change",
                                "error": (
                                    "repair 결과의 code가 "
                                    "원본 code와 동일합니다."
                                ),
                                "returncode": None
                            }
                        else:
                            print(
                                "\n[REPAIR EXECUTION]"
                            )

                            repair_result = execute_code(
                                language=language,
                                code=repaired_code
                            )




                        # ================================================

                        # 수리 성공

                        # ================================================



                        if repair_result.get("success"):



                            print(

                                "\n[REPAIR] ✅ 수정 성공"

                            )



                            generated = repaired

                            result = repair_result

                            was_repaired = True



                        # ================================================

                        # 수리 실패

                        # ================================================



                        else:



                            print(

                                "\n[REPAIR] ❌ 수정 실패"

                            )



                            print_execution_failure(

                                repair_result

                            )



                            save_generation_failure(

                                generated=repaired,

                                plan=plan,

                                examples=examples,

                                result=repair_result,

                                attempt=attempt,

                                failure_type="repair"

                            )



                            print(

                                "\n수정에도 실패했습니다."

                            )



                            print(

                                "동일한 출제 계획으로 "

                                "새 문제를 생성합니다."

                            )



                            continue



                    except Exception as repair_error:



                        print(

                            "\n[REPAIR] 수정 과정 자체가 실패했습니다."

                        )



                        print(

                            repair_error

                        )



                        print(

                            "동일한 출제 계획으로 "

                            "새 문제를 생성합니다."

                        )



                        continue



                # --------------------------------------------

                # 실제 실행 결과

                # --------------------------------------------



                actual_output = (

                    result.get(

                        "output",

                        ""

                    )

                )



                # 아무것도 출력하지 않는 문제는 폐기

                if actual_output == "":



                    print(

                        "\n[EXECUTION] ❌ "

                        "프로그램 출력이 없습니다."

                    )



                    print(

                        "현재 문제를 "

                        "폐기합니다."

                    )



                    continue



                # ============================================

                # 핵심

                #

                # Qwen이 만든 정답이 아니라

                # 실제 실행 결과를 정답으로 확정

                # ============================================



                generated[

                    "answer"

                ] = actual_output



                print(

                    "\n[EXECUTION] "

                    "✅ 실행 성공"

                )



                print(

                    "실제 출력:"

                )



                print(

                    repr(

                        actual_output

                    )

                )



                print(

                    "→ 실제 실행 결과를 "

                    "정답으로 확정합니다."

                )



            # ------------------------------------------------

            # 메타데이터

            # ------------------------------------------------



            generated = (

                add_generation_metadata(

                    generated,

                    plan,

                    examples

                )

            )



            # ------------------------------------------------

            # 검증 완료 표시

            # ------------------------------------------------



            if (

                generated.get(

                    "type"

                )

                == "code_output"

            ):



                generated[

                    "validation"

                ] = {



                    "status":

                        "verified",



                    "method":

                        "execution",



                    "language":

                        normalize_text(

                            generated.get(

                                "language"

                            )

                        ),



                    "generation_attempt":

                        attempt,

                    "repaired": was_repaired

                }



            else:



                generated[

                    "validation"

                ] = {



                    "status":

                        "unverified",



                    "method":

                        None

                }



            print(

                "\n[SUCCESS] "

                "검증된 문제 생성 완료"

            )



            return generated



        # ----------------------------------------------------

        # 생성 형식 문제

        # ----------------------------------------------------



        except ValueError as e:



            print(

                "\n[GENERATION ERROR]"

            )



            print(

                e

            )



            if (

                attempt

                < max_attempts

            ):



                print(

                    "현재 문제를 폐기하고 "

                    "다시 생성합니다."

                )



                continue



            raise



        # ----------------------------------------------------

        # 환경 자체 문제

        # ----------------------------------------------------



        except RuntimeError as e:



            print(

                "\n[RUNTIME ERROR]"

            )



            print(

                e

            )



            raise



    raise RuntimeError(

        f"{max_attempts}번 시도했지만 "

        "실행 가능한 문제를 "

        "생성하지 못했습니다."

    )





# ============================================================

# 생성 문제 저장

# ============================================================



def save_generated_question(
    question
):

    # --------------------------------------------------------
    # 언어별 저장 경로 결정
    # --------------------------------------------------------

    language = normalize_text(
        question.get("language")
    )

    (
        language_dir,
        generated_file,
        _
    ) = get_language_output_paths(language)

    language_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # 기존 생성 문제 불러오기
    # --------------------------------------------------------

    existing = []

    if generated_file.exists():

        with open(
            generated_file,
            "r",
            encoding="utf-8"
        ) as f:

            try:
                existing = json.load(f)

            except json.JSONDecodeError:
                existing = []

    if not isinstance(
        existing,
        list
    ):
        existing = []

    # --------------------------------------------------------
    # 코드 문제 안전장치
    # --------------------------------------------------------

    if (
        question.get("type")
        == "code_output"
    ):

        validation = (
            question.get(
                "validation",
                {}
            )
        )

        if (
            validation.get(
                "status"
            )
            != "verified"
        ):

            raise ValueError(
                "검증되지 않은 코드 문제는 "
                "저장할 수 없습니다."
            )

        # 정답 자체가 없는 경우도 저장 금지
        if (
            "answer"
            not in question
        ):

            raise ValueError(
                "실행 결과 정답이 없는 "
                "코드 문제는 저장할 수 없습니다."
            )

    # --------------------------------------------------------
    # 새 문제 추가
    # --------------------------------------------------------

    existing.append(
        question
    )

    # --------------------------------------------------------
    # 언어별 JSON 파일에 저장
    # --------------------------------------------------------

    with open(
        generated_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            existing,
            f,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"\n[SAVE] 생성 문제 저장 완료 "
        f"(누적 {len(existing)}문제)"
    )

    print(
        f"[SAVE PATH] {generated_file}"
    )





# ============================================================

# 실행

# ============================================================



def main():



    print(

        "======================================"

    )



    print(

        " 정보처리기사 문제 생성기"

    )



    print(

        f" 모델: {CHAT_MODEL}"

    )



    print(

        "======================================"

    )



    # --------------------------------------------------------

    # 기출 데이터

    # --------------------------------------------------------



    questions = load_questions()



    print(

        f"\n기출 문제 "

        f"{len(questions)}개 "

        f"로드 완료"

    )



    print(

        f"기출 데이터 폴더: "

        f"{EXAMPLES_DIR}"

    )



    # ========================================================

    # 언어 선택

    # ========================================================



    selected_language = (

        select_language()

    )



    print(

        "\n[선택된 언어]"

    )



    print(

        selected_language

    )



    # ========================================================

    # 생성 개수 입력

    # ========================================================



    while True:



        try:



            generation_count = int(

                input(

                    "\n생성할 문제 수를 입력하세요: "

                ).strip()

            )



            if generation_count <= 0:



                print(

                    "1 이상의 숫자를 입력해주세요."

                )



                continue



            break



        except ValueError:



            print(

                "숫자를 입력해주세요."

            )



    print(

        f"\n{selected_language} 문제 "

        f"{generation_count}개를 생성합니다."

    )



    # ========================================================

    # 전체 생성 통계

    # ========================================================



    success_count = 0

    failure_count = 0



    # ========================================================

    # 여러 문제 생성

    # ========================================================



    for problem_index in range(

        1,

        generation_count + 1

    ):



        print(

            "\n\n"

            "######################################"

        )



        print(

            f" 문제 {problem_index}"

            f"/{generation_count}"

        )



        print(

            "######################################"

        )



        # ====================================================

        # 중요:

        # 문제마다 seed를 새로 선택한다.

        # ====================================================



        seed_question = (

            select_seed_question(

                questions=questions,

                language=selected_language

            )

        )



        selected_concepts = (

            seed_question.get(

                "concepts",

                []

            )

        )



        print(

            "\n[출제 유형 seed]"

        )



        print(

            seed_question.get("id")

        )



        print(

            "[이번 문제 출제 개념]"

        )



        print(

            ", ".join(

                selected_concepts

            )

        )



        # ====================================================

        # 생성 계획

        # ====================================================



        plan = {



            "type":

                "code_output",



            "subject":

                "프로그래밍",



            "language":

                selected_language,



            "concepts":

                selected_concepts

        }



        print(

            "\n[생성 계획]"

        )



        print(

            json.dumps(

                plan,

                ensure_ascii=False,

                indent=2

            )

        )



        print(

            "\n문제를 생성하고 있습니다..."

        )



        # ====================================================

        # 문제 생성

        # ====================================================



        try:



            generated = (

                generate_question(



                    questions=questions,



                    plan=plan,



                    example_count=3,



                    max_attempts=5

                )

            )



        except RuntimeError as e:



            failure_count += 1



            print(

                "\n[BATCH FAILURE]"

            )



            print(

                f"{problem_index}번 문제 생성 실패"

            )



            print(

                e

            )



            print(

                "다음 문제 생성을 계속합니다."

            )



            continue



        except ValueError as e:



            failure_count += 1



            print(

                "\n[BATCH FAILURE]"

            )



            print(

                f"{problem_index}번 문제 생성 실패"

            )



            print(

                e

            )



            print(

                "다음 문제 생성을 계속합니다."

            )



            continue



        # ====================================================

        # 결과 출력

        # ====================================================



        print(

            "\n======================================"

        )



        print(

            f" 생성 결과 "

            f"{problem_index}/{generation_count}"

        )



        print(

            "======================================"

        )



        print(

            json.dumps(

                generated,

                ensure_ascii=False,

                indent=2

            )

        )



        # ====================================================

        # 저장

        # ====================================================



        save_generated_question(

            generated

        )



        success_count += 1



    # ========================================================

    # 최종 결과

    # ========================================================



    print(

        "\n\n"

        "======================================"

    )



    print(

        " 전체 생성 완료"

    )



    print(

        "======================================"

    )



    print(

        f"요청 문제 수: "

        f"{generation_count}"

    )



    print(

        f"생성 성공: "

        f"{success_count}"

    )



    print(

        f"생성 실패: "

        f"{failure_count}"

    )



    # ========================================================
    # 최종 저장 경로
    # ========================================================

    (
        language_dir,
        generated_file,
        failure_log_file
    ) = get_language_output_paths(
        selected_language
    )

    # ========================================================
    # 최종 결과
    # ========================================================

    print(
        "\n\n"
        "======================================"
    )

    print(
        " 전체 생성 완료"
    )

    print(
        "======================================"
    )

    print(
        f"요청 문제 수: "
        f"{generation_count}"
    )

    print(
        f"생성 성공: "
        f"{success_count}"
    )

    print(
        f"생성 실패: "
        f"{failure_count}"
    )

    print(
        f"문제 저장 파일: "
        f"{generated_file}"
    )

    print(
        f"실패 로그 파일: "
        f"{failure_log_file}"
    )





if __name__ == "__main__":

    main()