import "server-only";

type DemoEnvironment = { nodeEnv?: string; deploymentEnv?: string; enabled?: string };

export function canAccessPivotDemo({ nodeEnv, deploymentEnv, enabled }: DemoEnvironment): boolean {
  if (deploymentEnv === "production") return false;
  if (nodeEnv === "development" && (!deploymentEnv || deploymentEnv === "development")) return true;
  return nodeEnv === "production" && deploymentEnv === "preview" && enabled === "true";
}
