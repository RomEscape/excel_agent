"""oc_relay — kimdaeri 중계 서버(content-blind WebSocket 릴레이).

데스크톱과 모바일이 각각 아웃바운드로 relay에 접속하고(둘 다 NAT 뒤),
relay는 pairing_id로 두 소켓을 짝지어 프레임을 그대로 브리지한다.
payload는 절대 해석하지 않는다(Zero-Trust: 평문/비밀은 사용자 기기를 떠나지 않는다).
"""
