import 'package:citysoul_app/main.dart';
import 'package:citysoul_app/services/player_api.dart';
import 'package:citysoul_app/services/secure_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fakes.dart';

void main() {
  testWidgets(
    'app boots straight to home without any login/registration UI',
    (WidgetTester tester) async {
      final fakeSecureStore = FakeSecureStore();
      final fakePlayerApi = FakePlayerApi();

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            secureStoreProvider.overrideWithValue(fakeSecureStore),
            playerApiProvider.overrideWithValue(fakePlayerApi),
          ],
          child: const CitySoulApp(),
        ),
      );

      // Startup screen shows a loading indicator, not a login form.
      expect(find.byType(TextField), findsNothing);
      expect(find.textContaining('登入'), findsNothing);
      expect(find.textContaining('註冊'), findsNothing);

      // Let the async bootstrap + navigation settle.
      await tester.pumpAndSettle();

      expect(fakePlayerApi.callCount, 1);
      expect(find.text('城市靈魂'), findsOneWidget);
      expect(find.byType(TextField), findsNothing);
    },
  );

  testWidgets(
    'shows a retry option when identity bootstrap fails',
    (WidgetTester tester) async {
      final fakeSecureStore = FakeSecureStore();
      final fakePlayerApi = FakePlayerApi(shouldFail: true);

      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            secureStoreProvider.overrideWithValue(fakeSecureStore),
            playerApiProvider.overrideWithValue(fakePlayerApi),
          ],
          child: const CitySoulApp(),
        ),
      );

      await tester.pumpAndSettle();

      expect(find.text('重試'), findsOneWidget);
    },
  );
}
