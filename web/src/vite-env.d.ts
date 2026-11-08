/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 原生包（Capacitor iOS/安卓）编译时注入：部署后的后端基址，须含 /api/v1 后缀。
   *  例：https://ai.your-domain.com/api/v1 。Web 自托管不设置，走相对 /api/v1。 */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
