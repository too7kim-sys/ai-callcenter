"""FAQ 지식베이스. 답변 추천과 AI 챗봇의 근거 자료로 사용된다."""

FAQS = [
    {
        "id": 1,
        "category": "배송",
        "question": "배송은 얼마나 걸리나요?",
        "answer": "주문 후 영업일 기준 2~3일 내에 배송됩니다. 도서산간 지역은 1~2일 추가될 수 있습니다.",
        "keywords": ["배송", "언제", "도착", "며칠", "발송", "택배"],
    },
    {
        "id": 2,
        "category": "배송",
        "question": "배송 조회는 어떻게 하나요?",
        "answer": "마이페이지 > 주문내역에서 운송장 번호를 확인하실 수 있으며, 택배사 홈페이지에서 실시간 조회가 가능합니다.",
        "keywords": ["배송조회", "운송장", "송장", "추적", "어디"],
    },
    {
        "id": 3,
        "category": "환불/교환",
        "question": "환불은 어떻게 신청하나요?",
        "answer": "마이페이지 > 주문내역에서 환불을 신청하실 수 있으며, 상품 수령 후 7일 이내에 신청하셔야 합니다. 환불 처리는 영업일 기준 3~5일 소요됩니다.",
        "keywords": ["환불", "반품", "돈", "취소", "되돌려"],
    },
    {
        "id": 4,
        "category": "환불/교환",
        "question": "교환은 가능한가요?",
        "answer": "상품 불량 또는 오배송의 경우 무료 교환이 가능합니다. 단순 변심에 의한 교환은 왕복 배송비가 부과됩니다.",
        "keywords": ["교환", "사이즈", "색상", "변심", "바꾸"],
    },
    {
        "id": 5,
        "category": "결제",
        "question": "결제 수단은 무엇이 있나요?",
        "answer": "신용카드, 계좌이체, 간편결제(카카오페이·네이버페이), 무통장입금을 지원합니다.",
        "keywords": ["결제수단", "카드", "페이", "입금", "계좌"],
    },
    {
        "id": 6,
        "category": "결제",
        "question": "결제가 안 돼요.",
        "answer": "카드 한도 또는 브라우저 문제일 수 있습니다. 다른 결제 수단을 이용하시거나 잠시 후 다시 시도해 주세요. 문제가 지속되면 카드사에 문의 부탁드립니다.",
        "keywords": ["결제오류", "결제안", "결제 안", "오류", "에러", "안돼"],
    },
    {
        "id": 7,
        "category": "회원/계정",
        "question": "비밀번호를 잊어버렸어요.",
        "answer": "로그인 화면의 '비밀번호 찾기'를 통해 가입 시 등록한 이메일로 재설정 링크를 받으실 수 있습니다.",
        "keywords": ["비밀번호", "로그인", "계정", "비번", "찾기"],
    },
    {
        "id": 8,
        "category": "회원/계정",
        "question": "회원 탈퇴는 어떻게 하나요?",
        "answer": "마이페이지 > 회원정보 > 회원탈퇴에서 진행하실 수 있습니다. 탈퇴 시 적립금과 쿠폰은 소멸됩니다.",
        "keywords": ["탈퇴", "회원", "해지", "그만"],
    },
    {
        "id": 9,
        "category": "제품",
        "question": "제품 보증 기간은 얼마인가요?",
        "answer": "구매일 기준 1년간 무상 A/S가 제공됩니다. 보증서와 구매 영수증을 보관해 주세요.",
        "keywords": ["보증", "as", "a/s", "수리", "고장", "불량"],
    },
    {
        "id": 10,
        "category": "기타",
        "question": "상담원과 직접 통화하고 싶어요.",
        "answer": "평일 09:00~18:00 고객센터(1588-0000)로 전화 주시거나, 채팅에서 '상담원 연결'을 요청해 주세요. 빠르게 도와드리겠습니다.",
        "keywords": ["상담원", "통화", "전화", "사람", "직접"],
    },
]

CATEGORIES = ["배송", "환불/교환", "결제", "회원/계정", "제품", "기타"]


def search(text, limit=3):
    """텍스트와 키워드가 겹치는 FAQ를 점수 순으로 반환한다."""
    text = (text or "").lower()
    scored = []
    for item in FAQS:
        score = sum(1 for kw in item["keywords"] if kw.lower() in text)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def as_prompt_text():
    """AI 프롬프트에 삽입할 FAQ 텍스트."""
    lines = []
    for item in FAQS:
        lines.append(f"- [{item['category']}] Q: {item['question']}\n  A: {item['answer']}")
    return "\n".join(lines)
