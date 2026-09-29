// Consumer navigation stays separate from the team's real API screens.
export const consumerRoutes = {
  home: "/consumer",
  login: "/consumer/login",
  onboarding: "/consumer/onboarding",
  saved: "/consumer/saved",
  addTopic: "/consumer/topics/new",
  content: (contentId: string) =>
    `/consumer/contents/${encodeURIComponent(contentId)}`,
} as const;
