import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'features/home/home_screen.dart';
import 'features/startup/startup_screen.dart';

void main() {
  runApp(const ProviderScope(child: CitySoulApp()));
}

/// 每個 ProviderScope（正式 App 一個、每個測試各一個）都會拿到獨立的
/// GoRouter 實例，避免測試之間共用同一個路由狀態（例如上一個測試已經
/// 導到 /home，下一個測試卻預期從 / 開始）。
final routerProvider = Provider<GoRouter>((ref) {
  return GoRouter(
    initialLocation: '/',
    routes: [
      GoRoute(
        path: '/',
        builder: (context, state) => StartupScreen(
          onReady: (context) => context.go('/home'),
        ),
      ),
      GoRoute(
        path: '/home',
        builder: (context, state) => const HomeScreen(),
      ),
    ],
  );
});

class CitySoulApp extends ConsumerWidget {
  const CitySoulApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp.router(
      title: '城市靈魂',
      theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: Colors.deepPurple)),
      routerConfig: ref.watch(routerProvider),
    );
  }
}
