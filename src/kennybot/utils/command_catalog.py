from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpSection:
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class SlashCommandMeta:
    name: str
    description: str
    category: str


HELP_SECTIONS: tuple[HelpSection, ...] = (
    HelpSection(
        title="会話機能",
        lines=(
            "- Botへのメンション/返信で会話応答",
            "- DMでもそのまま会話可能",
            "- 会話時は直近100件の履歴を参照",
            "- 天気・日付・祝日は外部API参照で案内可能",
            "- キーワード自動リアクション",
            "- スパム検知と自動処罰",
        ),
    ),
    HelpSection(
        title="案内・検索機能",
        lines=(
            "- Bot自身の機能説明や使い方を案内可能",
            "- 過去の会話内容を補助的に参照して応答可能",
            "- リモート接続 + APIキー構成では必要時のみ `web search` / `web fetch` を使用",
            "- 最新情報が必要な質問では、検索未実施なら検索したふりをしない",
        ),
    ),
    HelpSection(
        title="チャンネル知識",
        lines=(
            "- チャンネル固有のQ&Aを蓄積して参照可能",
            "- 追加したQ&Aは会話応答や機能説明の文脈で参照される",
            "- 管理者が設定可能",
        ),
    ),
    HelpSection(
        title="議事録機能",
        lines=(
            "- `/minutes` の action で start/stop/status を切り替え",
            "- VC参加者が開始できる",
            "- VC無人または停止コマンドで終了する",
            "- APIキー不要の Google Web Speech を優先して文字起こし",
            "- Google失敗時だけ faster-whisper にフォールバック",
            "- 音声を文字起こしし、長文はAI要約して投稿",
            "- 投稿時はコマンド実行者をメンション",
        ),
    ),
    HelpSection(
        title="kenny-chat 連携",
        lines=(
            "- 各サーバーに `kenny-chat` チャンネルを作ると相互中継",
            "- 表示名は発言者の頭文字のみ",
            "- 元発言を削除すると中継先の投稿も削除",
        ),
    ),
    HelpSection(
        title="ログ機能",
        lines=(
            "- `voice-events`: VC入退室ログ",
            "- `member-events`: 参加/退出ログ",
        ),
    ),
    HelpSection(
        title="読み上げ機能",
        lines=(
            "- `/tts` の action で join/leave/voice を切り替え",
            "- `tts status` は廃止",
        ),
    ),
    HelpSection(
        title="予定機能",
        lines=(
            "- `/birthday` の action で add/list/remove を切り替え",
            "- 誕生日は月日だけでも登録可能",
            "- 通知時刻も HH:MM で指定可能",
        ),
    ),
)

COMMAND_CATEGORY_ORDER: tuple[str, ...] = (
    "基本",
    "要約・設定",
    "ナレッジ",
    "議事録",
    "ロール",
    "モデレーション",
    "予定",
    "読み上げ",
    "ゲーム・ユーティリティ",
)

SLASH_COMMANDS: dict[str, SlashCommandMeta] = {
    "help": SlashCommandMeta(
        name="help",
        description="Botで使える機能とコマンドを表示",
        category="基本",
    ),
    "bot_info": SlashCommandMeta(
        name="bot_info",
        description="Bot状態と疎通確認を表示",
        category="基本",
    ),
    "ping": SlashCommandMeta(
        name="ping",
        description="Bot の応答速度を確認",
        category="基本",
    ),
    "summarize_recent": SlashCommandMeta(
        name="summarize_recent",
        description="このチャンネルの直近メッセージをAI要約",
        category="要約・設定",
    ),
    "set_recent_window": SlashCommandMeta(
        name="set_recent_window",
        description="チャット要約の既定件数を設定",
        category="要約・設定",
    ),
    "config": SlashCommandMeta(
        name="config",
        description="設定の表示・更新",
        category="要約・設定",
    ),
    "model_list": SlashCommandMeta(
        name="model_list",
        description="利用可能なモデル一覧を表示（ローカル/リモート）",
        category="要約・設定",
    ),
    "model_change": SlashCommandMeta(
        name="model_change",
        description="Bot が使うモデルを切り替え",
        category="要約・設定",
    ),
    "minutes": SlashCommandMeta(
        name="minutes",
        description="議事録モードの開始・停止・状態表示",
        category="議事録",
    ),
    "reaction_role_set": SlashCommandMeta(
        name="reaction_role_set",
        description="メッセージのリアクションにロール付与を紐付け",
        category="ロール",
    ),
    "reaction_role_remove": SlashCommandMeta(
        name="reaction_role_remove",
        description="リアクションロール設定を解除",
        category="ロール",
    ),
    "reaction_role_list": SlashCommandMeta(
        name="reaction_role_list",
        description="リアクションロール設定を一覧表示",
        category="ロール",
    ),
    "modpanel": SlashCommandMeta(
        name="modpanel",
        description="スパム管理パネルを作成",
        category="モデレーション",
    ),
    "birthday": SlashCommandMeta(
        name="birthday",
        description="誕生日の登録・一覧・削除・通知時刻設定",
        category="予定",
    ),
    "tts": SlashCommandMeta(
        name="tts",
        description="読み上げの開始・停止・話者変更",
        category="読み上げ",
    ),
    "game": SlashCommandMeta(
        name="game",
        description="ミニゲームを開始（リアクション参加）",
        category="ゲーム・ユーティリティ",
    ),
    "timer": SlashCommandMeta(
        name="timer",
        description="タイマーを開始（時/分/秒指定）",
        category="ゲーム・ユーティリティ",
    ),
    "vc_control": SlashCommandMeta(
        name="vc_control",
        description="VCミュート操作パネルを作成",
        category="ゲーム・ユーティリティ",
    ),
    "group_match": SlashCommandMeta(
        name="group_match",
        description="リアクション参加で2人組/3人組を自動作成",
        category="ゲーム・ユーティリティ",
    ),
    "vrchat_world": SlashCommandMeta(
        name="vrchat_world",
        description="VRChat のワールドを検索",
        category="ゲーム・ユーティリティ",
    ),
    "vrc_user": SlashCommandMeta(
        name="vrc_user",
        description="VRChat ユーザーURLからプロフィールを取得",
        category="ゲーム・ユーティリティ",
    ),
}


def get_slash_command_meta(key: str) -> SlashCommandMeta:
    return SLASH_COMMANDS[key]
