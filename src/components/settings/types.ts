import type { AppConfig, ConnectionStatus } from "../../lib/types";

/** 设置面板里的一个模型档位（后端 model profile 的视图形状）。 */
export interface ModelItem {
  id: string;
  name: string;
  model: string;
  base_url: string;
  api_key?: string;
  provider: string;
  is_active: boolean;
}

/** 各 tab 共用的回调：往面板顶部的状态条推一条消息。 */
export type Notify = (message: string) => void;

export interface TabProps {
  onConfigUpdate: (config: AppConfig) => void;
  notify: Notify;
}

export interface PersonaTabProps extends TabProps {
  config: AppConfig | null;
  connectionStatus: ConnectionStatus;
  activeModelName: string | null;
}
