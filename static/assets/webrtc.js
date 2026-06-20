/*!
 * 콜센터 WebRTC 통화 클라이언트 (브라우저↔브라우저 P2P 음성).
 *
 * 사용법:
 *   const call = new CallClient({
 *     role: "customer" | "agent",
 *     callId: 42,
 *     conversationId: 7,
 *     onState: (state) => {...},   // 'connecting' | 'connected' | 'ended'
 *     onError: (err) => {...},
 *   });
 *   await call.start();   // 마이크 켜기 + offer 생성(고객) 또는 대기(상담원)
 *   call.handleSignal(event);  // SSE 에서 받은 'call_signal' 이벤트
 *   call.hangup();              // 정상 종료
 *
 * 서버 시그널링: POST /api/calls/{id}/signal { kind, payload }
 *               SSE 이벤트 'call_signal' { from, kind, payload }
 *
 * STUN: Google 공개 서버. NAT 통과의 90%+ 케이스에 충분.
 */
(function () {
  const ICE_SERVERS = [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: "stun:stun1.l.google.com:19302" },
  ];

  class CallClient {
    constructor(opts) {
      this.role = opts.role;                    // 'customer' | 'agent'
      this.callId = opts.callId;
      this.conversationId = opts.conversationId;
      // 익명 고객은 customer_token 으로 /signal 권한 검증. 상담원은 세션 쿠키.
      this.customerToken = opts.customerToken || null;
      this.onState = opts.onState || (() => {});
      this.onError = opts.onError || (() => {});
      this.pc = null;
      this.localStream = null;
      this.remoteAudio = null;
      this._pendingIce = [];   // 원격 SDP 도착 전 ICE 임시 보관
      this._gotRemote = false;
      this._ended = false;
    }

    /** 마이크 권한 요청 + PeerConnection 생성. customer 라면 offer 도 생성·전송. */
    async start({ isCaller }) {
      try {
        this.localStream = await navigator.mediaDevices.getUserMedia({
          audio: true, video: false,
        });
      } catch (e) {
        this.onError(new Error("마이크 권한이 필요합니다: " + (e.message || e.name)));
        throw e;
      }

      this.pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
      this.localStream.getTracks().forEach((t) => this.pc.addTrack(t, this.localStream));

      this.pc.onicecandidate = (e) => {
        if (e.candidate) this._send("ice", { candidate: e.candidate });
      };
      this.pc.ontrack = (e) => {
        if (!this.remoteAudio) {
          this.remoteAudio = document.createElement("audio");
          this.remoteAudio.autoplay = true;
          this.remoteAudio.playsInline = true;
          document.body.appendChild(this.remoteAudio);
        }
        this.remoteAudio.srcObject = e.streams[0];
        this.onState("connected");
      };
      this.pc.onconnectionstatechange = () => {
        const s = this.pc.connectionState;
        if (s === "connected") this.onState("connected");
        else if (s === "failed") {
          // ICE/연결 실패 — 서버에 알려서 상대방 UI 도 정리되도록
          if (!this._ended) {
            this.onError(new Error("연결 실패 (NAT 또는 네트워크 문제)"));
            this.hangup();
          }
        } else if (s === "disconnected" || s === "closed") {
          if (!this._ended) this.onState("ended");
        }
      };
      this.onState("connecting");

      if (isCaller) {
        const offer = await this.pc.createOffer();
        await this.pc.setLocalDescription(offer);
        this._send("offer", { sdp: this.pc.localDescription });
      }
    }

    /** SSE 'call_signal' 이벤트 처리. 같은 call 의 반대편 메시지만 적용. */
    async handleSignal(ev) {
      if (!this.pc || this._ended) return;
      if (ev.from === this.role) return;            // 자기 자신은 무시
      if (ev.call_id != null && ev.call_id !== this.callId) return;

      try {
        if (ev.kind === "offer") {
          await this.pc.setRemoteDescription(new RTCSessionDescription(ev.payload.sdp));
          await this._drainPendingIce();
          const answer = await this.pc.createAnswer();
          await this.pc.setLocalDescription(answer);
          this._send("answer", { sdp: this.pc.localDescription });
          this._gotRemote = true;
        } else if (ev.kind === "answer") {
          await this.pc.setRemoteDescription(new RTCSessionDescription(ev.payload.sdp));
          await this._drainPendingIce();
          this._gotRemote = true;
        } else if (ev.kind === "ice") {
          if (this._gotRemote) {
            await this.pc.addIceCandidate(new RTCIceCandidate(ev.payload.candidate));
          } else {
            this._pendingIce.push(ev.payload.candidate);
          }
        } else if (ev.kind === "hangup") {
          this.onState("ended");
          this.cleanup();
        }
      } catch (e) {
        this.onError(e);
      }
    }

    async _drainPendingIce() {
      for (const c of this._pendingIce) {
        try { await this.pc.addIceCandidate(new RTCIceCandidate(c)); }
        catch (e) { /* 무시 — late candidate */ }
      }
      this._pendingIce = [];
    }

    _send(kind, payload) {
      // 시그널링 자체가 막히면 통화 성립 불가 — 실패 시 디버그 로그.
      const body = { kind, payload };
      if (this.customerToken) body.customer_token = this.customerToken;
      fetch(`/api/calls/${this.callId}/signal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(body),
      }).then((r) => {
        if (!r.ok) console.warn("[call] signal", kind, "→ HTTP", r.status);
      }).catch((e) => {
        console.warn("[call] signal", kind, "실패:", e);
      });
    }

    async hangup() {
      if (this._ended) return;
      this._ended = true;
      try {
        await fetch(`/api/calls/${this.callId}/end`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: this.role + "_hangup" }),
        });
      } catch (e) { /* 무시 */ }
      this.cleanup();
      this.onState("ended");
    }

    cleanup() {
      this._ended = true;
      try { if (this.localStream) this.localStream.getTracks().forEach((t) => t.stop()); } catch (_) {}
      try { if (this.pc) this.pc.close(); } catch (_) {}
      try { if (this.remoteAudio) { this.remoteAudio.srcObject = null; this.remoteAudio.remove(); } } catch (_) {}
      this.localStream = null;
      this.pc = null;
      this.remoteAudio = null;
    }
  }

  window.CallClient = CallClient;
})();
