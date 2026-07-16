import 'dart:convert';

import 'package:http/http.dart' as http;

/// 데스크톱 QR이 전달하는 페어링 정보.
///
/// QR 페이로드 계약(데스크톱이 이 형식으로 렌더):
///   {"v":1, "relay":"http://…", "pairing_id":"…", "code":"…"}
class PairingInfo {
  final String relayUrl;
  final String pairingId;
  final String code;
  const PairingInfo({
    required this.relayUrl,
    required this.pairingId,
    required this.code,
  });
}

class PairingException implements Exception {
  final String message;
  PairingException(this.message);
  @override
  String toString() => message;
}

/// QR 페이로드(JSON 문자열) → PairingInfo. 형식이 틀리면 PairingException.
PairingInfo parseQrPayload(String raw) {
  Object? decoded;
  try {
    decoded = jsonDecode(raw);
  } catch (_) {
    throw PairingException('QR 형식이 올바르지 않습니다(JSON 아님)');
  }
  if (decoded is! Map) throw PairingException('QR 형식이 올바르지 않습니다');
  final relay = (decoded['relay'] as String?)?.trim();
  final pid = (decoded['pairing_id'] as String?)?.trim();
  final code = (decoded['code'] as String?)?.trim();
  if (relay == null ||
      relay.isEmpty ||
      pid == null ||
      pid.isEmpty ||
      code == null ||
      code.isEmpty) {
    throw PairingException('QR에 relay/pairing_id/code가 없습니다');
  }
  return PairingInfo(relayUrl: relay, pairingId: pid, code: code);
}

/// relay에 /pair/complete를 호출해 1:1 바인딩을 확정한다(모바일 측 페어링 완료).
Future<void> completePairing(PairingInfo info, {http.Client? client}) async {
  final c = client ?? http.Client();
  try {
    final resp = await c
        .post(
          Uri.parse('${info.relayUrl}/pair/complete'),
          headers: {'content-type': 'application/json'},
          body: jsonEncode({'code': info.code}),
        )
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode != 200) {
      throw PairingException('페어링 실패 (HTTP ${resp.statusCode})');
    }
  } finally {
    if (client == null) c.close();
  }
}
