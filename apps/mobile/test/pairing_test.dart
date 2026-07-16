import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:officeclaw_mobile/pairing/pairing_service.dart';

void main() {
  test('정상 페이로드 파싱', () {
    final raw = jsonEncode({
      'v': 1,
      'relay': 'http://127.0.0.1:8787',
      'pairing_id': 'p1',
      'code': 'abc123',
    });
    final info = parseQrPayload(raw);
    expect(info.relayUrl, 'http://127.0.0.1:8787');
    expect(info.pairingId, 'p1');
    expect(info.code, 'abc123');
  });

  test('JSON이 아니면 예외', () {
    expect(() => parseQrPayload('not json'), throwsA(isA<PairingException>()));
  });

  test('필수 필드 누락이면 예외', () {
    final raw = jsonEncode({'relay': 'http://x', 'pairing_id': 'p1'}); // code 없음
    expect(() => parseQrPayload(raw), throwsA(isA<PairingException>()));
  });

  test('빈 값이면 예외', () {
    final raw = jsonEncode({'relay': '', 'pairing_id': 'p1', 'code': 'c'});
    expect(() => parseQrPayload(raw), throwsA(isA<PairingException>()));
  });
}
