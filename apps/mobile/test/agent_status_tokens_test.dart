import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:officeclaw_mobile/protocol/protocol.dart';
import 'package:officeclaw_mobile/theme/brand_palette.dart';
import 'package:officeclaw_mobile/theme/brand_theme.dart';
import 'package:officeclaw_mobile/theme/agent_status_tokens.dart';

void main() {
  const light = AgentStatusColors.light;
  const dark = AgentStatusColors.dark;

  group('resolveAgentStatus', () {
    test('idle은 연결 여부로 라벨이 갈린다', () {
      expect(
        resolveAgentStatus(
          state: AgentState.idle,
          connected: true,
          colors: light,
        ).label,
        '연결됨',
      );
      expect(
        resolveAgentStatus(
          state: AgentState.idle,
          connected: false,
          colors: light,
        ).label,
        '대기',
      );
    });

    test('연결 여부와 무관하게 상태가 우선한다', () {
      for (final connected in [true, false]) {
        expect(
          resolveAgentStatus(
            state: AgentState.thinking,
            connected: connected,
            colors: light,
          ).label,
          '생각 중',
        );
        expect(
          resolveAgentStatus(
            state: AgentState.remoteControlling,
            connected: connected,
            colors: light,
          ).label,
          '제어 중',
        );
        expect(
          resolveAgentStatus(
            state: AgentState.offline,
            connected: connected,
            colors: light,
          ).label,
          '오프라인',
        );
      }
    });

    test('모든 AgentState가 토큰을 갖는다 (enum 추가 시 여기서 걸린다)', () {
      for (final state in AgentState.values) {
        final token = resolveAgentStatus(
          state: state,
          connected: true,
          colors: light,
        );
        expect(token.label, isNotEmpty);
      }
    });
  });

  group('브랜드 색 규칙', () {
    test('연결됨은 브랜드 초록을 그대로 쓴다', () {
      expect(light.connected, BrandPalette.core);
      expect(dark.connected, BrandPalette.lift);
    });

    test('주의가 필요한 상태는 브랜드 초록과 겹치지 않는다', () {
      for (final colors in [light, dark]) {
        for (final attention in [colors.thinking, colors.remoteControlling]) {
          expect(
            attention,
            isNot(colors.connected),
            reason: '주의 상태가 "연결됨"과 같은 색이면 구분이 안 된다',
          );
          expect(
            attention,
            isNot(BrandPalette.core),
            reason: '브랜드 초록은 건강한 상태 전용이다',
          );
        }
      }
    });
  });

  group('buildBrandTheme', () {
    test('라이트/다크 모두 AgentStatusColors를 싣는다', () {
      expect(
        buildBrandTheme(Brightness.light).extension<AgentStatusColors>(),
        AgentStatusColors.light,
      );
      expect(
        buildBrandTheme(Brightness.dark).extension<AgentStatusColors>(),
        AgentStatusColors.dark,
      );
    });

    test('ColorScheme이 브랜드 시드에서 파생된다', () {
      expect(
        buildBrandTheme(Brightness.light).colorScheme.brightness,
        Brightness.light,
      );
      expect(
        buildBrandTheme(Brightness.dark).colorScheme.brightness,
        Brightness.dark,
      );
    });
  });
}
