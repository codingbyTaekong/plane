import type { LucideIcon } from "lucide-react";
import { ArrowDownToLine, ArrowUpToLine, Building, CreditCard, Users, Webhook } from "lucide-react";
// plane imports
import type { ISvgIcons } from "@plane/propel/icons";
import type { TWorkspaceSettingsTabs } from "@plane/types";

export const WORKSPACE_SETTINGS_ICONS: Record<TWorkspaceSettingsTabs, LucideIcon | React.FC<ISvgIcons>> = {
  general: Building,
  members: Users,
  export: ArrowUpToLine,
  imports: ArrowDownToLine,
  "billing-and-plans": CreditCard,
  webhooks: Webhook,
};
