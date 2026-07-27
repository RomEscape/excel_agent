import 'package:flutter/material.dart';

/// 김대리 브랜드 원색 토큰.
///
/// 값은 전부 브랜드 SVG의 `fill`·`stop-color`에서 그대로 뽑았다
/// (`assets/brand-wordmark.svg`, `apps/desktop/src/assets/brand-logo-{light,dark}.svg`).
/// **여기서 새 색을 만들지 않는다** — 새 브랜드 색이 필요하면 SVG를 먼저 고치고 그 값을 옮긴다.
class BrandPalette {
  const BrandPalette._();

  /// 코어 그린. M3 ColorScheme 전체가 이 시드 하나에서 파생된다.
  static const Color core = Color(0xFF2DB400);

  /// 그라디언트 밝은쪽. 다크 지면 위에서 코어 대신 쓴다.
  static const Color lift = Color(0xFF46C642);

  /// 그라디언트 최상단.
  static const Color pale = Color(0xFF82C642);

  /// 그라디언트 어두운쪽.
  static const Color deep = Color(0xFF015F00);

  /// 그라디언트 최하단.
  static const Color darkest = Color(0xFF0B3F0A);

  /// 라이트 로고 타일 지면.
  static const Color surfaceLight = Color(0xFFF9FDF7);

  /// 다크 로고 타일 지면.
  static const Color surfaceDark = Color(0xFF0C1909);

  /// 로고의 near-black green — 본문 잉크.
  static const Color ink = Color(0xFF092400);
}
