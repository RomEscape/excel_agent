import 'package:flutter/material.dart';

import 'brand_palette.dart';

/// AgentState 표시색.
///
/// 규칙: **브랜드 초록 = 건강한 상태**, 초록 밖의 색 = 사용자의 주의가 필요한 상태.
/// 그래서 `thinking`·`remoteControlling`만 브랜드 밖 색을 쓴다 — 초록 계열로 두면
/// "정상 동작 중"과 구분이 안 된다.
@immutable
class AgentStatusColors extends ThemeExtension<AgentStatusColors> {
  const AgentStatusColors({
    required this.connected,
    required this.thinking,
    required this.remoteControlling,
    required this.inactive,
  });

  /// 연결됨 — 브랜드 초록 그대로.
  final Color connected;

  /// 생각 중 — 앰버. 초록에 묻히지 않게 뺐다.
  final Color thinking;

  /// 제어 중 — 바이올렛. 내 PC를 실제로 만지는 순간이라 가장 멀리 뺀다.
  final Color remoteControlling;

  /// 대기·오프라인 — 중립.
  final Color inactive;

  static const AgentStatusColors light = AgentStatusColors(
    connected: BrandPalette.core,
    thinking: Color(0xFFC07C00),
    remoteControlling: Color(0xFF5B5BD6),
    inactive: Color(0xFF8A917F),
  );

  static const AgentStatusColors dark = AgentStatusColors(
    connected: BrandPalette.lift,
    thinking: Color(0xFFD4A044),
    remoteControlling: Color(0xFF8B8BE8),
    inactive: Color(0xFF74886E),
  );

  @override
  AgentStatusColors copyWith({
    Color? connected,
    Color? thinking,
    Color? remoteControlling,
    Color? inactive,
  }) {
    return AgentStatusColors(
      connected: connected ?? this.connected,
      thinking: thinking ?? this.thinking,
      remoteControlling: remoteControlling ?? this.remoteControlling,
      inactive: inactive ?? this.inactive,
    );
  }

  @override
  AgentStatusColors lerp(ThemeExtension<AgentStatusColors>? other, double t) {
    if (other is! AgentStatusColors) return this;
    return AgentStatusColors(
      connected: Color.lerp(connected, other.connected, t)!,
      thinking: Color.lerp(thinking, other.thinking, t)!,
      remoteControlling: Color.lerp(
        remoteControlling,
        other.remoteControlling,
        t,
      )!,
      inactive: Color.lerp(inactive, other.inactive, t)!,
    );
  }

  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other is AgentStatusColors &&
        other.connected == connected &&
        other.thinking == thinking &&
        other.remoteControlling == remoteControlling &&
        other.inactive == inactive;
  }

  @override
  int get hashCode =>
      Object.hash(connected, thinking, remoteControlling, inactive);
}

/// 브랜드 시드에서 파생한 M3 테마.
///
/// 표면·잉크·컨테이너 색을 하나하나 지정하지 않는다 —
/// [BrandPalette.core] 시드 하나만 주고 나머지는 M3가 파생하게 둔다.
/// 그래야 라이트/다크 대비가 알아서 맞고, 브랜드 색이 바뀌어도 한 줄만 고치면 된다.
ThemeData buildBrandTheme(Brightness brightness) {
  return ThemeData(
    useMaterial3: true,
    colorScheme: ColorScheme.fromSeed(
      seedColor: BrandPalette.core,
      brightness: brightness,
    ),
    extensions: <ThemeExtension<dynamic>>[
      brightness == Brightness.dark
          ? AgentStatusColors.dark
          : AgentStatusColors.light,
    ],
  );
}

ThemeData get brandLightTheme => buildBrandTheme(Brightness.light);

ThemeData get brandDarkTheme => buildBrandTheme(Brightness.dark);
