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
            "- Botへのメンション/返信でAI応答",
            "- DMでもそのままAI会話可能",
            "- 会話時は直近100件の履歴を参照",
            "- 天気・日付・祝日は外部API参照で案内可能",
            "- キーワード自動リアクション",
            "- スパム検知と自動処罰",
        ),
    ),
    HelpSection(
        title="議事録機能",
        lines=(
            "- VC参加者が `/minutes_start` で開始",
            "- `/minutes_stop` またはVC無人で停止",
            "- Google Speech-to-Text を優先して文字起こし",
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
            "- 12秒に1回まで発言可能",
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
)

COMMAND_CATEGORY_ORDER: tuple[str, ...] = (
    "基本",
    "要約・設定",
    "議事録",
    "ロール",
    "読み上げ",
    "ゲーム・VC",
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
    "config_show": SlashCommandMeta(
        name="config_show",
        description="設定値を表示",
        category="要約・設定",
    ),
    "config_set": SlashCommandMeta(
        name="config_set",
        description="設定値を更新",
        category="要約・設定",
    ),
    "ollama_pull": SlashCommandMeta(
        name="ollama_pull",
        description="Ollama でモデルを pull し、必要なら利用モデルへ設定",
        category="要約・設定",
    ),
    "ollama_use_model": SlashCommandMeta(
        name="ollama_use_model",
        description="Bot が使う Ollama モデルを切り替え",
        category="要約・設定",
    ),
    "minutes_start": SlashCommandMeta(
        name="minutes_start",
        description="議事録モードを開始（VC参加者のみ）",
        category="議事録",
    ),
    "minutes_stop": SlashCommandMeta(
        name="minutes_stop",
        description="議事録モードを停止して要約を作成",
        category="議事録",
    ),
    "minutes_status": SlashCommandMeta(
        name="minutes_status",
        description="議事録モードの状態を表示",
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
    "tts_join": SlashCommandMeta(
        name="tts_join",
        description="現在の通話チャンネルに参加し、このチャンネルを読み上げ対象にする",
        category="読み上げ",
    ),
    "tts_leave": SlashCommandMeta(
        name="tts_leave",
        description="読み上げを停止してVCから切断",
        category="読み上げ",
    ),
    "tts_voice": SlashCommandMeta(
        name="tts_voice",
        description="読み上げ話者IDを変更",
        category="読み上げ",
    ),
    "tts_status": SlashCommandMeta(
        name="tts_status",
        description="読み上げ状態を表示",
        category="読み上げ",
    ),
    "game": SlashCommandMeta(
        name="game",
        description="ミニゲームを開始（リアクション参加）",
        category="ゲーム・VC",
    ),
    "timer": SlashCommandMeta(
        name="timer",
        description="タイマーを開始（時/分/秒指定）",
        category="ゲーム・VC",
    ),
    "vc_control": SlashCommandMeta(
        name="vc_control",
        description="VCミュート操作パネルを作成",
        category="ゲーム・VC",
    ),
    "group_match": SlashCommandMeta(
        name="group_match",
        description="リアクション参加で2人組/3人組を自動作成",
        category="ゲーム・VC",
    ),
}


def get_slash_command_meta(key: str) -> SlashCommandMeta:
    return SLASH_COMMANDS[key]
