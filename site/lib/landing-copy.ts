import type { Locale } from './i18n';
import type { MarkKind } from '@/components/mark-tile';

export const usageMarks = [
  ['claude', 'capy'],
  ['claude', 'muse'],
  ['slack', 'claude-code'],
  ['github', 'grok-bot'],
  ['capy', 'claude-code'],
  ['telegram', 'claude'],
] as const satisfies readonly (readonly MarkKind[])[];

type Copy = {
  docs: string;
  about: string;
  license: string;
  trademarks: string;
  install: string;
  setupWithAgent: string;
  copy: string;
  copied: string;
  copyError: string;
  openSource: string;
  boardAlt: string;
  boardHint: string;
  positioning: { title: string; lead: string };
  deployment: {
    title: string;
    local: string; localIntro: string; localAlternative: string; localGuide: string;
    server: string; serverIntro: string; serverGuide: string;
    cloud: string; cloudIntro: string; cloudGuide: string;
    flyClaim: string; paidPlan: string; hobbyPlan: string; freePlan: string; tunnelAttribution: string;
  };
  usage: { title: string; examples: [string, string, string, string, string, string] };
  hero: { stem: string; endingPrefix?: string; endings: string[]; between: [string, string, string] };
};

export const landingCopy: Record<Locale, Copy> = {
  en: {
    docs: 'Docs',
    about: 'About',
    license: 'Apache-2.0',
    trademarks: 'Trademarks',
    install: 'uvx swb-one up',
    setupWithAgent: 'Set up with agent',
    copy: 'Copy',
    copied: 'Copied',
    copyError: 'Select the command to copy it',
    openSource: 'Free and open source under Apache-2.0',
    boardAlt: 'SwB connects your apps',
    boardHint: 'Select an app to set it up.',
    hero: {
      stem: 'First-party agents working together, without',
      endings: ['running CLIs', 'risking a ban', 'sharing session tokens', 'leaving their apps', 'moving them', 'scripting websites'],
      between: ['Real-time conversations between ', ' and ', ''],
    },
    positioning: {
      title: 'Every agent works in its own app',
      lead: 'SwB carries messages and replies between the apps you use.',
    },
    deployment: {
      title: 'Run SwB where you want',
      local: 'On your computer',
      localIntro: 'Start SwB and open its sign-in link.',
      localAlternative: 'Use pipx to keep SwB installed.',
      localGuide: 'Install guide',
      server: 'On your server',
      serverIntro: 'Run SwB with Docker Compose.',
      serverGuide: 'Compose',
      cloud: 'In the cloud',
      cloudIntro: 'Choose a cloud provider.',
      cloudGuide: 'Self-hosting guide',
      flyClaim: 'Free with monthly allowance',
      paidPlan: 'Paid plan',
      hobbyPlan: 'Hobby plan',
      freePlan: 'Free',
      tunnelAttribution: 'Cloudflare Tunnel and the Cloudflare logo are trademarks of Cloudflare, Inc.',
    },
    usage: {
      title: 'What you can do with SwB',
      examples: [
        'Ask Capy Captain to review Claude’s changes.',
        'Save a project decision in Claude and read it in Muse.',
        'Send a Slack thread to a coding agent.',
        'Send new GitHub issues to a triage agent.',
        'Send work from Capy Captain to a local Claude Code session.',
        'Read Claude’s reply in Telegram.',
      ],
    },
  },
  'zh-Hans': {
    docs: '文档',
    about: '关于',
    license: 'Apache-2.0',
    trademarks: '商标',
    install: 'uvx swb-one up',
    setupWithAgent: '用智能体设置',
    copy: '复制',
    copied: '已复制',
    copyError: '请选中命令后复制',
    openSource: '免费开源，采用 Apache-2.0 许可',
    boardAlt: 'SwB 连接各个应用',
    boardHint: '选择要设置的应用。',
    hero: {
      stem: '第一方智能体协同工作，',
      endingPrefix: '不必',
      endings: ['运行 CLI', '担心封号', '交出会话令牌', '离开自己的应用', '搬迁它们', '模拟网页操作'],
      between: ['', ' 与 ', ' 实时对话'],
    },
    positioning: {
      title: '每个智能体都在自己的应用里工作',
      lead: 'SwB 在你常用的应用之间传递消息和回复。',
    },
    deployment: {
      title: '按需部署 SwB',
      local: '本机运行',
      localIntro: '启动 SwB，打开它显示的登录链接。',
      localAlternative: '也可以用 pipx 安装 SwB。',
      localGuide: '安装指南',
      server: '服务器部署',
      serverIntro: '用 Docker Compose 启动 SwB。',
      serverGuide: '部署指南',
      cloud: '云端部署',
      cloudIntro: '选择云服务商。',
      cloudGuide: '自托管指南',
      flyClaim: '月度额度内免费',
      paidPlan: '付费套餐',
      hobbyPlan: 'Hobby 方案',
      freePlan: '免费',
      tunnelAttribution: 'Cloudflare Tunnel 与 Cloudflare 标志是 Cloudflare, Inc. 的商标。',
    },
    usage: {
      title: '用 SwB 完成日常工作',
      examples: [
        '请 Capy Captain 审查 Claude 的改动。',
        '用 Claude 保存项目决定，再用 Muse 查看。',
        '把 Slack 讨论串交给编程智能体。',
        '把 GitHub 的新议题交给分流智能体。',
        '用 Capy Captain 给本机的 Claude Code 会话分配任务。',
        '在 Telegram 查看 Claude 的回复。',
      ],
    },
  },
  'zh-Hant': {
    docs: '文件',
    about: '關於',
    license: 'Apache-2.0',
    trademarks: '商標',
    install: 'uvx swb-one up',
    setupWithAgent: '用代理設定',
    copy: '複製',
    copied: '已複製',
    copyError: '請選取指令後複製',
    openSource: '免費開源，採用 Apache-2.0 授權',
    boardAlt: 'SwB 連接各個應用程式',
    boardHint: '選取要設定的應用程式。',
    hero: {
      stem: '第一方代理協同工作，',
      endingPrefix: '不必',
      endings: ['執行 CLI', '擔心封號', '交出工作階段權杖', '離開自己的應用程式', '搬移它們', '模擬網頁操作'],
      between: ['', ' 與 ', ' 即時對話'],
    },
    positioning: {
      title: '每個代理都在自己的應用程式裡工作',
      lead: 'SwB 在你慣用的應用程式之間傳遞訊息和回覆。',
    },
    deployment: {
      title: '選擇 SwB 的部署方式',
      local: '在本機執行',
      localIntro: '啟動 SwB，開啟它顯示的登入連結。',
      localAlternative: '也可以用 pipx 安裝 SwB。',
      localGuide: '安裝指南',
      server: '在伺服器上執行',
      serverIntro: '用 Docker Compose 啟動 SwB。',
      serverGuide: '部署指南',
      cloud: '在雲端執行',
      cloudIntro: '選擇雲端服務商。',
      cloudGuide: '自行代管指南',
      flyClaim: '每月免費額度內免費',
      paidPlan: '付費方案',
      hobbyPlan: 'Hobby 方案',
      freePlan: '免費',
      tunnelAttribution: 'Cloudflare Tunnel 與 Cloudflare 標誌是 Cloudflare, Inc. 的商標。',
    },
    usage: {
      title: '用 SwB 完成日常工作',
      examples: [
        '請 Capy Captain 審查 Claude 的變更。',
        '用 Claude 儲存專案決策，再用 Muse 查看。',
        '把 Slack 討論串交給程式開發代理。',
        '把 GitHub 的新議題交給分流代理。',
        '用 Capy Captain 把工作交給本機的 Claude Code 工作階段。',
        '在 Telegram 查看 Claude 的回覆。',
      ],
    },
  },
  es: {
    "docs": "Docs",
    "about": "Acerca de",
    "license": "Apache-2.0",
    "trademarks": "Marcas comerciales",
    "install": "uvx swb-one up",
    "setupWithAgent": "Configurar con un agente",
    "copy": "Copiar",
    "copied": "Copiado",
    "copyError": "Selecciona el comando para copiarlo",
    "openSource": "Gratis y de código abierto con licencia Apache-2.0",
    "boardAlt": "SwB conecta tus apps",
    "boardHint": "Selecciona una app para configurarla.",
    "hero": {
      "stem": "Agentes oficiales de cada plataforma que trabajan juntos,",
      "endingPrefix": "sin ",
      "endings": [
        "ejecutar CLI",
        "riesgo de bloqueo",
        "ceder tokens de sesión",
        "salir de sus apps",
        "migrarlos",
        "automatizar webs"
      ],
      "between": [
        "Conversaciones en tiempo real entre ",
        " y ",
        ""
      ]
    },
    "positioning": {
      "title": "Cada agente trabaja en su propia app",
      "lead": "SwB lleva mensajes y respuestas entre las apps que usas."
    },
    "deployment": {
      "title": "Ejecuta SwB donde quieras",
      "local": "En tu ordenador",
      "localIntro": "Inicia SwB y abre su enlace de inicio de sesión.",
      "localAlternative": "Usa pipx para mantener SwB instalado.",
      "localGuide": "Guía de instalación",
      "server": "En tu servidor",
      "serverIntro": "Ejecuta SwB con Docker Compose.",
      "serverGuide": "Compose",
      "cloud": "En la nube",
      "cloudIntro": "Elige un proveedor de nube.",
      "cloudGuide": "Guía de autoalojamiento",
      "flyClaim": "Gratis dentro de la cuota mensual",
      "paidPlan": "Plan de pago",
      "hobbyPlan": "Plan Hobby",
      "freePlan": "Gratis",
      "tunnelAttribution": "Cloudflare Tunnel y el logotipo de Cloudflare son marcas comerciales de Cloudflare, Inc."
    },
    "usage": {
      "title": "Qué puedes hacer con SwB",
      "examples": [
        "Pide a Capy Captain que revise los cambios de Claude.",
        "Guarda una decisión del proyecto en Claude y léela en Muse.",
        "Envía un hilo de Slack a un agente de programación.",
        "Envía las issues nuevas de GitHub a un agente de triaje.",
        "Envía trabajo de Capy Captain a una sesión local de Claude Code.",
        "Lee la respuesta de Claude en Telegram."
      ]
    }
  },
  fr: {
    "docs": "Docs",
    "about": "À propos",
    "license": "Apache-2.0",
    "trademarks": "Marques",
    "install": "uvx swb-one up",
    "setupWithAgent": "Configurer avec un agent",
    "copy": "Copier",
    "copied": "Copié",
    "copyError": "Sélectionnez la commande pour la copier",
    "openSource": "Gratuit et open source sous licence Apache-2.0",
    "boardAlt": "SwB connecte vos applications",
    "boardHint": "Sélectionnez une application pour la configurer.",
    "hero": {
      "stem": "Des agents officiels qui collaborent,",
      "endingPrefix": "sans ",
      "endings": [
        "lancer de CLI",
        "risque de bannissement",
        "partager de jetons",
        "quitter leur application",
        "les déplacer",
        "automatiser le navigateur"
      ],
      "between": [
        "Conversations en temps réel entre ",
        " et ",
        ""
      ]
    },
    "positioning": {
      "title": "Chaque agent travaille dans son application",
      "lead": "SwB transmet les messages et les réponses entre les applications que vous utilisez."
    },
    "deployment": {
      "title": "Lancez SwB où vous voulez",
      "local": "Sur votre ordinateur",
      "localIntro": "Démarrez SwB et ouvrez son lien de connexion.",
      "localAlternative": "Installez SwB durablement avec pipx.",
      "localGuide": "Guide d'installation",
      "server": "Sur votre serveur",
      "serverIntro": "Lancez SwB avec Docker Compose.",
      "serverGuide": "Compose",
      "cloud": "Dans le cloud",
      "cloudIntro": "Choisissez un fournisseur cloud.",
      "cloudGuide": "Guide d'auto-hébergement",
      "flyClaim": "Gratuit avec quota mensuel",
      "paidPlan": "Offre payante",
      "hobbyPlan": "Offre Hobby",
      "freePlan": "Gratuit",
      "tunnelAttribution": "Cloudflare Tunnel et le logo Cloudflare sont des marques de Cloudflare, Inc."
    },
    "usage": {
      "title": "Ce que vous pouvez faire avec SwB",
      "examples": [
        "Demandez à Capy Captain de relire les modifications de Claude.",
        "Enregistrez une décision de projet dans Claude et lisez-la dans Muse.",
        "Envoyez un fil Slack à un agent de code.",
        "Envoyez les nouveaux tickets GitHub à un agent de tri.",
        "Transmettez une tâche de Capy Captain à une session Claude Code locale.",
        "Lisez la réponse de Claude dans Telegram."
      ]
    }
  },
  de: {
    "docs": "Doku",
    "about": "Über SwB",
    "license": "Apache-2.0",
    "trademarks": "Marken",
    "install": "uvx swb-one up",
    "setupWithAgent": "Per Agent einrichten",
    "copy": "Kopieren",
    "copied": "Kopiert",
    "copyError": "Markieren Sie den Befehl, um ihn zu kopieren",
    "openSource": "Kostenlos und Open Source unter Apache-2.0",
    "boardAlt": "SwB verbindet Ihre Apps",
    "boardHint": "Wählen Sie eine App zum Einrichten aus.",
    "hero": {
      "stem": "Offizielle Agenten arbeiten zusammen,",
      "endingPrefix": "ohne ",
      "endings": [
        "CLIs zu starten",
        "Kontosperren zu riskieren",
        "Sitzungstokens zu teilen",
        "ihre Apps zu verlassen",
        "sie zu migrieren",
        "Websites fernzusteuern"
      ],
      "between": [
        "Echtzeit-Unterhaltungen zwischen ",
        " und ",
        ""
      ]
    },
    "positioning": {
      "title": "Jeder Agent arbeitet in seiner eigenen App",
      "lead": "SwB überträgt Nachrichten und Antworten zwischen den Apps, die Sie nutzen."
    },
    "deployment": {
      "title": "SwB betreiben, wo Sie möchten",
      "local": "Auf Ihrem Computer",
      "localIntro": "Starten Sie SwB und öffnen Sie den Anmeldelink.",
      "localAlternative": "Mit pipx bleibt SwB dauerhaft installiert.",
      "localGuide": "Installationsanleitung",
      "server": "Auf Ihrem Server",
      "serverIntro": "Betreiben Sie SwB mit Docker Compose.",
      "serverGuide": "Compose",
      "cloud": "In der Cloud",
      "cloudIntro": "Wählen Sie einen Cloud-Anbieter aus.",
      "cloudGuide": "Anleitung zum Selbsthosten",
      "flyClaim": "Kostenlos mit Monatskontingent",
      "paidPlan": "Kostenpflichtig",
      "hobbyPlan": "Hobby-Plan",
      "freePlan": "Kostenlos",
      "tunnelAttribution": "Cloudflare Tunnel und das Cloudflare-Logo sind Marken von Cloudflare, Inc."
    },
    "usage": {
      "title": "Was Sie mit SwB tun können",
      "examples": [
        "Bitten Sie Capy Captain, die Änderungen von Claude zu prüfen.",
        "Speichern Sie eine Projektentscheidung in Claude und lesen Sie sie in Muse.",
        "Senden Sie einen Slack-Thread an einen Coding-Agenten.",
        "Senden Sie neue GitHub-Issues an einen Triage-Agenten.",
        "Senden Sie Aufgaben von Capy Captain an eine lokale Claude-Code-Sitzung.",
        "Lesen Sie die Antwort von Claude in Telegram."
      ]
    }
  },
  ko: {
    "docs": "문서",
    "about": "소개",
    "license": "Apache-2.0",
    "trademarks": "상표",
    "install": "uvx swb-one up",
    "setupWithAgent": "에이전트로 설정",
    "copy": "복사",
    "copied": "복사됨",
    "copyError": "명령을 선택해 복사하세요",
    "openSource": "Apache-2.0 라이선스의 무료 오픈 소스",
    "boardAlt": "SwB이 내 앱들을 연결합니다",
    "boardHint": "설정할 앱을 선택하세요.",
    "hero": {
      "stem": "각 플랫폼의 자체 에이전트 협업,",
      "endings": [
        "CLI 실행 없이",
        "계정 정지 걱정 없이",
        "세션 토큰 공유 없이",
        "각자의 앱에서 그대로",
        "옮길 필요 없이",
        "웹사이트 자동화 없이"
      ],
      "between": [
        "",
        ", ",
        " 간 실시간 대화"
      ]
    },
    "positioning": {
      "title": "모든 에이전트는 자기 앱에서 일합니다",
      "lead": "SwB이 내가 쓰는 앱들 사이에서 메시지와 답장을 전달합니다."
    },
    "deployment": {
      "title": "원하는 곳에서 SwB 실행",
      "local": "내 컴퓨터",
      "localIntro": "SwB을 시작하고 로그인 링크를 여세요.",
      "localAlternative": "SwB을 설치해 두고 쓰려면 pipx를 사용하세요.",
      "localGuide": "설치 가이드",
      "server": "내 서버",
      "serverIntro": "Docker Compose로 SwB을 실행하세요.",
      "serverGuide": "Compose",
      "cloud": "클라우드",
      "cloudIntro": "클라우드 제공업체를 고르세요.",
      "cloudGuide": "자체 호스팅 가이드",
      "flyClaim": "월 무료 사용량 제공",
      "paidPlan": "유료 플랜",
      "hobbyPlan": "Hobby 플랜",
      "freePlan": "무료",
      "tunnelAttribution": "Cloudflare Tunnel과 Cloudflare 로고는 Cloudflare, Inc.의 상표입니다."
    },
    "usage": {
      "title": "SwB으로 할 수 있는 일",
      "examples": [
        "Capy Captain에게 Claude의 변경 사항을 검토해 달라고 요청하세요.",
        "프로젝트 결정 사항을 Claude에 저장하고 Muse에서 읽으세요.",
        "Slack 스레드를 코딩 에이전트에게 보내세요.",
        "새 GitHub 이슈를 분류 에이전트에게 보내세요.",
        "Capy Captain의 작업을 로컬 Claude Code 세션으로 보내세요.",
        "Claude의 답장을 Telegram에서 읽으세요."
      ]
    }
  },
  ja: {
    "docs": "ドキュメント",
    "about": "SwB について",
    "license": "Apache-2.0",
    "trademarks": "商標",
    "install": "uvx swb-one up",
    "setupWithAgent": "エージェントでセットアップ",
    "copy": "コピー",
    "copied": "コピーしました",
    "copyError": "コマンドを選択してコピーしてください",
    "openSource": "Apache-2.0 ライセンスの無料オープンソース",
    "boardAlt": "SwB がアプリをつなぎます",
    "boardHint": "アプリを選択してセットアップしてください。",
    "hero": {
      "stem": "公式のエージェント同士が連携。",
      "endings": [
        "CLI の実行は不要",
        "BAN の心配は不要",
        "セッションの共有は不要",
        "アプリの乗り換えは不要",
        "移行作業は不要",
        "Web の自動操作は不要"
      ],
      "between": [
        "",
        " と ",
        " がリアルタイムで会話"
      ]
    },
    "positioning": {
      "title": "エージェントはそれぞれのアプリで働きます",
      "lead": "SwB は、普段使うアプリの間でメッセージと返信を受け渡します。"
    },
    "deployment": {
      "title": "SwB を好きな場所で実行",
      "local": "自分のパソコン",
      "localIntro": "SwB を起動して、サインインリンクを開いてください。",
      "localAlternative": "pipx を使うと、SwB をインストールしておけます。",
      "localGuide": "インストールガイド",
      "server": "自分のサーバー",
      "serverIntro": "Docker Compose で SwB を実行してください。",
      "serverGuide": "Compose",
      "cloud": "クラウド",
      "cloudIntro": "クラウドプロバイダーを選んでください。",
      "cloudGuide": "セルフホスティングガイド",
      "flyClaim": "毎月の無料枠あり",
      "paidPlan": "有料プラン",
      "hobbyPlan": "Hobby プラン",
      "freePlan": "無料",
      "tunnelAttribution": "Cloudflare Tunnel および Cloudflare のロゴは、Cloudflare, Inc. の商標です。"
    },
    "usage": {
      "title": "SwB でできること",
      "examples": [
        "Claude の変更のレビューを Capy Captain に頼む。",
        "プロジェクトで決めたことを Claude で保存し、Muse で読む。",
        "Slack のスレッドをコーディングエージェントに送る。",
        "GitHub の新しい Issue をトリアージ用のエージェントに送る。",
        "Capy Captain からローカルの Claude Code のセッションに作業を送る。",
        "Claude の返信を Telegram で読む。"
      ]
    }
  },
};
