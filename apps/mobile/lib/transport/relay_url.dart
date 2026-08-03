/// relay 주소 정책 — 스킴 검증·정규화의 단일 소스(순수 모듈).
///
/// **왜 코드가 이걸 책임지나**: 안드로이드 `usesCleartextTraffic`·네트워크 보안 설정과
/// iOS ATS는 *플랫폼이 소유한* 소켓에만 적용된다. Flutter 공식 문서가 명시하듯
/// "If the socket is owned by Dart/Flutter, no policy will be enforced" — 이 앱의
/// 네트워크는 package:http(페어링)와 web_socket_channel(WS) 둘 다 Dart 소유 소켓이라
/// 매니페스트/plist로는 평문을 막을 수 없다. 그래서 릴리스 빌드의 TLS 강제는
/// 이 모듈 한 곳이 담당한다.
///
/// QR 스캔·수동 입력 어느 경로로 들어오든 relay 주소는 여기를 통과해야 한다.
library;

import 'package:flutter/foundation.dart' show kReleaseMode;

/// 평문(http/ws) relay 허용 여부의 기본값 — debug·profile 빌드에서만 true.
///
/// 개발 중엔 로컬 relay에 인증서가 없어 http로 붙어야 하고, 배포판은 wss만 허용한다.
const bool kAllowInsecureRelayByDefault = !kReleaseMode;

const Set<String> _secureSchemes = {'https', 'wss'};
const Set<String> _insecureSchemes = {'http', 'ws'};

final RegExp _schemePrefix = RegExp(r'^[a-zA-Z][a-zA-Z0-9+.\-]*://');
final RegExp _trailingSlashes = RegExp(r'/+$');

class RelayUrlException implements Exception {
  final String message;
  RelayUrlException(this.message);
  @override
  String toString() => message;
}

/// relay 주소를 검증하고 HTTP 베이스 URL로 정규화한다.
///
/// - 스킴이 없으면 붙여준다: 평문 허용 시 `http://`, 금지 시 `https://`.
/// - `ws`/`wss`로 들어와도 `http`/`https` 베이스로 통일한다(페어링 REST와 공유하므로).
/// - 끝의 `/`는 제거한다 — 호출부가 `$base/pair/complete` 식으로 이어붙이기 때문.
/// - [allowInsecure]가 false인데 평문 스킴이면 [RelayUrlException].
String normalizeRelayBaseUrl(
  String raw, {
  bool allowInsecure = kAllowInsecureRelayByDefault,
}) {
  final trimmed = raw.trim();
  if (trimmed.isEmpty) {
    throw RelayUrlException('relay 주소가 비어 있습니다');
  }

  final withScheme = _schemePrefix.hasMatch(trimmed)
      ? trimmed
      : '${allowInsecure ? 'http' : 'https'}://$trimmed';

  final uri = Uri.tryParse(withScheme);
  if (uri == null || uri.host.isEmpty) {
    throw RelayUrlException('relay 주소 형식이 올바르지 않습니다: $raw');
  }

  final scheme = uri.scheme.toLowerCase();
  final isSecure = _secureSchemes.contains(scheme);
  if (!isSecure && !_insecureSchemes.contains(scheme)) {
    throw RelayUrlException('지원하지 않는 relay 스킴입니다: $scheme');
  }
  if (!isSecure && !allowInsecure) {
    throw RelayUrlException('보안 연결(https/wss)만 사용할 수 있습니다 — 평문 주소: $raw');
  }

  final normalized = uri.replace(scheme: isSecure ? 'https' : 'http');
  return normalized.toString().replaceFirst(_trailingSlashes, '');
}

/// 정규화된 베이스 URL로부터 모바일 레그 WebSocket URL을 만든다.
///
/// `https://` → `wss://`, `http://` → `ws://`. 평문 정책은 [normalizeRelayBaseUrl]이
/// 이미 적용한다.
String relayMobileWsUrl(
  String rawBaseUrl,
  String pairingId, {
  bool allowInsecure = kAllowInsecureRelayByDefault,
}) {
  final base = normalizeRelayBaseUrl(rawBaseUrl, allowInsecure: allowInsecure);
  final ws = base.startsWith('https://')
      ? 'wss://${base.substring('https://'.length)}'
      : 'ws://${base.substring('http://'.length)}';
  return '$ws/ws/mobile?pairing_id=${Uri.encodeQueryComponent(pairingId)}';
}
