import 'dart:async';

import 'package:citysoul_app/services/location_service.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fakes.dart';

void main() {
  group('LocationService', () {
    test('defaults to a 30 second poll interval', () {
      final service = LocationService(provider: FakeLocationProvider());

      expect(service.pollInterval, const Duration(seconds: 30));

      service.dispose();
    });

    test('start() immediately fetches a position without waiting for a tick', () async {
      final provider = FakeLocationProvider();
      final ticks = StreamController<void>();
      final service = LocationService(provider: provider, ticks: ticks.stream);

      service.start();
      await Future<void>.delayed(Duration.zero);

      expect(provider.callCount, 1);

      service.dispose();
      await ticks.close();
    });

    test('each tick triggers another position fetch', () async {
      final provider = FakeLocationProvider();
      final ticks = StreamController<void>();
      final service = LocationService(provider: provider, ticks: ticks.stream);

      service.start();
      await Future<void>.delayed(Duration.zero);
      expect(provider.callCount, 1);

      ticks.add(null);
      await Future<void>.delayed(Duration.zero);
      expect(provider.callCount, 2);

      ticks.add(null);
      await Future<void>.delayed(Duration.zero);
      expect(provider.callCount, 3);

      service.dispose();
      await ticks.close();
    });

    test('stop() halts further fetches even if ticks keep arriving', () async {
      final provider = FakeLocationProvider();
      final ticks = StreamController<void>();
      final service = LocationService(provider: provider, ticks: ticks.stream);

      service.start();
      await Future<void>.delayed(Duration.zero);
      final callCountBeforeStop = provider.callCount;

      service.stop();
      ticks.add(null);
      ticks.add(null);
      await Future<void>.delayed(Duration.zero);

      expect(provider.callCount, callCountBeforeStop);
      expect(service.isPolling, isFalse);

      service.dispose();
      await ticks.close();
    });

    test('positionStream emits the fetched position', () async {
      final provider = FakeLocationProvider();
      final ticks = StreamController<void>();
      final service = LocationService(provider: provider, ticks: ticks.stream);

      final positions = <double>[];
      final subscription = service.positionStream.listen((p) => positions.add(p.latitude));

      service.start();
      await Future<void>.delayed(Duration.zero);

      expect(positions, [25.0955]);

      await subscription.cancel();
      service.dispose();
      await ticks.close();
    });
  });
}
