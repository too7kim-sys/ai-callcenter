"""Locust 기반 부하 테스트 시나리오.

사용법:
    pip install locust
    locust -f scripts/loadtest_locust.py --host=http://localhost:8000
    → 브라우저로 http://localhost:8089 접속, 사용자 수/스폰 비율 입력

시나리오:
  • 고객 5명 : 상담원 1명 비율 (실제 운영 대략 비율)
  • 고객 — 채팅 시작 → 메시지 3~5개 송신 → 종료
  • 상담원 — 콘솔/대시보드/콜백 큐 폴링 + 상담 상세 조회
"""
import json
import random

try:
    from locust import HttpUser, task, between
except ImportError:
    raise SystemExit("locust 가 필요합니다: pip install locust")


CUSTOMER_MESSAGES = [
    "안녕하세요",
    "환불 처리는 어떻게 하나요?",
    "주문번호 12345 배송 상태 확인해 주세요",
    "결제 취소 부탁드립니다",
    "비밀번호 분실했어요",
    "감사합니다",
]


class CustomerUser(HttpUser):
    """익명 고객 — 채팅 시작 → 메시지 송신 → 종료."""
    wait_time = between(1, 4)
    weight = 5

    def on_start(self):
        r = self.client.post(
            "/api/conversations",
            json={"customer_name": f"고객{random.randint(1, 9999)}"},
            name="POST /api/conversations",
        )
        self.conv_id = r.json().get("id") if r.ok else None
        self.messages_sent = 0

    @task(5)
    def send_chat(self):
        if not self.conv_id:
            return
        self.client.post(
            f"/api/conversations/{self.conv_id}/chat",
            json={"message": random.choice(CUSTOMER_MESSAGES)},
            name="POST /api/conversations/{id}/chat",
        )
        self.messages_sent += 1
        if self.messages_sent >= random.randint(3, 6):
            self.client.post(
                f"/api/conversations/{self.conv_id}/end",
                json={"rating": random.randint(3, 5)},
                name="POST /api/conversations/{id}/end",
            )
            self.conv_id = None
            self.messages_sent = 0

    @task(2)
    def fetch_conversation(self):
        if not self.conv_id:
            return
        self.client.get(
            f"/api/conversations/{self.conv_id}",
            name="GET /api/conversations/{id}",
        )


class AgentUser(HttpUser):
    """상담원 — 콘솔/대시보드/콜백 폴링."""
    wait_time = between(2, 8)
    weight = 1

    def on_start(self):
        r = self.client.post("/api/auth/login",
                             json={"username": "admin", "password": "admin1234"})
        if not r.ok:
            self.environment.runner.quit()

    @task(4)
    def list_conversations(self):
        self.client.get("/api/conversations", name="GET /api/conversations")

    @task(2)
    def dashboard_home(self):
        self.client.get("/api/dashboard", name="GET /api/dashboard")

    @task(2)
    def dashboard_kpi(self):
        self.client.get("/api/dashboard/kpi?days=7", name="GET /api/dashboard/kpi")

    @task(1)
    def dashboard_agents(self):
        self.client.get("/api/dashboard/agents?days=30", name="GET /api/dashboard/agents")

    @task(1)
    def callback_summary(self):
        self.client.get("/api/callbacks/summary", name="GET /api/callbacks/summary")
