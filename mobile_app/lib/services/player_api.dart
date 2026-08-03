import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'api_client.dart';

/// 對應後端 `PlayerResponse`（見 SDD 第8.1節）。
class PlayerIdentity {
  const PlayerIdentity({
    required this.playerId,
    required this.deviceId,
    required this.accountId,
    required this.sessionToken,
  });

  final String playerId;
  final String deviceId;
  final String? accountId;
  final String sessionToken;

  factory PlayerIdentity.fromJson(Map<String, dynamic> json) {
    return PlayerIdentity(
      playerId: json['player_id'] as String,
      deviceId: json['device_id'] as String,
      accountId: json['account_id'] as String?,
      sessionToken: json['session_token'] as String,
    );
  }
}

/// `POST /api/v1/players` 的抽象介面（seam）。
///
/// 跟 [SecureStore] 一樣，抽成介面是為了讓 [IdentityBootstrapService] 的
/// 測試不需要真的打網路請求或起本機後端，只需要一個回傳固定值的 fake。
abstract class PlayerApi {
  Future<PlayerIdentity> createOrGetPlayer(String deviceId);
}

class HttpPlayerApi implements PlayerApi {
  HttpPlayerApi(this._dio);

  final Dio _dio;

  @override
  Future<PlayerIdentity> createOrGetPlayer(String deviceId) async {
    final response = await _dio.post(
      '/api/v1/players',
      data: {'device_id': deviceId},
    );
    return PlayerIdentity.fromJson(response.data as Map<String, dynamic>);
  }
}

final playerApiProvider = Provider<PlayerApi>((ref) {
  return HttpPlayerApi(ref.watch(dioProvider));
});
