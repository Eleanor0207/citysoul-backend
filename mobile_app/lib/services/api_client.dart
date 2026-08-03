import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/app_config.dart';
import 'secure_store.dart';

/// Sprint1 範圍只需要「自動帶 session_token」這一件事。Encounter/Sense
/// Token 的攔截與 429 fallback 文案轉換（見《Flutter前端架構》第3節網路層
/// 設計）留到對話/召喚相關功能落地時再加，這裡先不做，避免預先刻用不到的邏輯。
const sessionTokenStorageKey = 'session_token';

final dioProvider = Provider<Dio>((ref) {
  final dio = Dio(BaseOptions(baseUrl: AppConfig.apiBaseUrl));
  final secureStore = ref.watch(secureStoreProvider);

  dio.interceptors.add(
    InterceptorsWrapper(
      onRequest: (options, handler) async {
        final token = await secureStore.read(sessionTokenStorageKey);
        if (token != null) {
          options.headers['Authorization'] = 'Bearer $token';
        }
        handler.next(options);
      },
    ),
  );

  return dio;
});
