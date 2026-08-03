import 'package:flutter/material.dart';

/// 佔位首頁。真正的探索地圖畫面（S7）留待後續 ticket，這裡只證明
/// 「啟動→建立匿名身分→進入App」這條端到端路徑走得通。
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(child: Text('城市靈魂')),
    );
  }
}
