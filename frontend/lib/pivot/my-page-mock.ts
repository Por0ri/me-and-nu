import { maskProfileEmail, type MyPageDataSource } from "./my-page";

// Reserved example.com domain; no real person's email or login session.
export const mockMyPageSource: MyPageDataSource = {
  kind: "demo",
  async load() {
    return { status: "ready", profile: { origin: "demo", nickname: "미뉴", maskedEmail: maskProfileEmail("demo.preview@example.com") } };
  },
};
