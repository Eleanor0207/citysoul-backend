import 'package:citysoul_app/services/api_client.dart';
import 'package:citysoul_app/services/identity_bootstrap_service.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fakes.dart';

void main() {
  group('IdentityBootstrapService', () {
    test('generates a device_id when none exists and stores it', () async {
      final secureStore = FakeSecureStore();
      final playerApi = FakePlayerApi();
      final service = IdentityBootstrapService(
        secureStore: secureStore,
        playerApi: playerApi,
      );

      await service.bootstrap();

      final storedDeviceId = await secureStore.read(deviceIdStorageKey);
      expect(storedDeviceId, isNotNull);
      expect(playerApi.lastDeviceId, storedDeviceId);
    });

    test('reuses existing device_id on subsequent launches', () async {
      final secureStore = FakeSecureStore();
      await secureStore.write(deviceIdStorageKey, 'existing-device-id');
      final playerApi = FakePlayerApi();
      final service = IdentityBootstrapService(
        secureStore: secureStore,
        playerApi: playerApi,
      );

      await service.bootstrap();

      expect(playerApi.callCount, 1);
      expect(playerApi.lastDeviceId, 'existing-device-id');
      final storedDeviceId = await secureStore.read(deviceIdStorageKey);
      expect(storedDeviceId, 'existing-device-id');
    });

    test('stores the session_token returned by the API', () async {
      final secureStore = FakeSecureStore();
      final playerApi = FakePlayerApi();
      final service = IdentityBootstrapService(
        secureStore: secureStore,
        playerApi: playerApi,
      );

      final identity = await service.bootstrap();

      final storedToken = await secureStore.read(sessionTokenStorageKey);
      expect(storedToken, identity.sessionToken);
    });

    test('propagates errors from the API without storing a token', () async {
      final secureStore = FakeSecureStore();
      final playerApi = FakePlayerApi(shouldFail: true);
      final service = IdentityBootstrapService(
        secureStore: secureStore,
        playerApi: playerApi,
      );

      await expectLater(service.bootstrap(), throwsException);

      final storedToken = await secureStore.read(sessionTokenStorageKey);
      expect(storedToken, isNull);
    });
  });
}
