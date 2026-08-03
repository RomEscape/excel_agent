import 'package:flutter_test/flutter_test.dart';
import 'package:officeclaw_mobile/transport/relay_url.dart';

/// 릴리스 빌드의 TLS 강제는 플랫폼 설정이 아니라 이 모듈이 책임진다(순수 함수).
/// 테스트는 항상 debug 모드로 도니 kAllowInsecureRelayByDefault에 기대지 않고
/// allowInsecure를 명시해서 양쪽 정책을 모두 고정한다.
void main() {
  group('normalizeRelayBaseUrl — 평문 허용(개발 빌드)', () {
    test('http 주소를 그대로 통과시킨다', () {
      expect(
        normalizeRelayBaseUrl('http://127.0.0.1:8787', allowInsecure: true),
        'http://127.0.0.1:8787',
      );
    });

    test('스킴이 없으면 http를 붙인다', () {
      expect(
        normalizeRelayBaseUrl('10.0.2.2:8787', allowInsecure: true),
        'http://10.0.2.2:8787',
      );
    });

    test('끝의 슬래시를 제거한다 — 호출부가 경로를 이어붙이기 때문', () {
      expect(
        normalizeRelayBaseUrl('http://127.0.0.1:8787/', allowInsecure: true),
        'http://127.0.0.1:8787',
      );
    });

    test('ws 스킴은 http 베이스로 통일한다', () {
      expect(
        normalizeRelayBaseUrl('ws://127.0.0.1:8787', allowInsecure: true),
        'http://127.0.0.1:8787',
      );
    });
  });

  group('normalizeRelayBaseUrl — 평문 금지(릴리스 빌드)', () {
    test('http 주소를 거부한다', () {
      expect(
        () => normalizeRelayBaseUrl(
          'http://127.0.0.1:8787',
          allowInsecure: false,
        ),
        throwsA(isA<RelayUrlException>()),
      );
    });

    test('ws 주소도 거부한다', () {
      expect(
        () => normalizeRelayBaseUrl(
          'ws://relay.example.com',
          allowInsecure: false,
        ),
        throwsA(isA<RelayUrlException>()),
      );
    });

    test('스킴이 없으면 https로 올린다', () {
      expect(
        normalizeRelayBaseUrl('relay.example.com', allowInsecure: false),
        'https://relay.example.com',
      );
    });

    test('https/wss는 통과하고 https 베이스로 통일된다', () {
      expect(
        normalizeRelayBaseUrl(
          'https://relay.example.com',
          allowInsecure: false,
        ),
        'https://relay.example.com',
      );
      expect(
        normalizeRelayBaseUrl('wss://relay.example.com', allowInsecure: false),
        'https://relay.example.com',
      );
    });

    test('경로 접두사는 보존한다', () {
      expect(
        normalizeRelayBaseUrl(
          'https://relay.example.com/oc',
          allowInsecure: false,
        ),
        'https://relay.example.com/oc',
      );
    });
  });

  group('normalizeRelayBaseUrl — 잘못된 입력', () {
    test('빈 문자열은 거부한다', () {
      expect(
        () => normalizeRelayBaseUrl('  ', allowInsecure: true),
        throwsA(isA<RelayUrlException>()),
      );
    });

    test('http/ws 이외의 스킴은 거부한다', () {
      expect(
        () => normalizeRelayBaseUrl(
          'ftp://relay.example.com',
          allowInsecure: true,
        ),
        throwsA(isA<RelayUrlException>()),
      );
    });

    test('호스트가 없으면 거부한다', () {
      expect(
        () => normalizeRelayBaseUrl('http://', allowInsecure: true),
        throwsA(isA<RelayUrlException>()),
      );
    });
  });

  group('relayMobileWsUrl', () {
    test('http 베이스는 ws로, https 베이스는 wss로 간다', () {
      expect(
        relayMobileWsUrl('http://127.0.0.1:8787', 'abc', allowInsecure: true),
        'ws://127.0.0.1:8787/ws/mobile?pairing_id=abc',
      );
      expect(
        relayMobileWsUrl(
          'https://relay.example.com',
          'abc',
          allowInsecure: false,
        ),
        'wss://relay.example.com/ws/mobile?pairing_id=abc',
      );
    });

    test('pairing_id를 쿼리 인코딩한다', () {
      expect(
        relayMobileWsUrl(
          'https://relay.example.com',
          'a b/c&d',
          allowInsecure: false,
        ),
        'wss://relay.example.com/ws/mobile?pairing_id=a+b%2Fc%26d',
      );
    });

    test('평문 금지 상태에서 http 베이스를 받으면 던진다 — WS 경로도 정책을 공유한다', () {
      expect(
        () => relayMobileWsUrl(
          'http://127.0.0.1:8787',
          'abc',
          allowInsecure: false,
        ),
        throwsA(isA<RelayUrlException>()),
      );
    });
  });
}
