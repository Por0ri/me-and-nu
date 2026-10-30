import { likeContent, unlikeContent, setPreference, clearPreference, type Preference } from "@/lib/api";
import { activityIdentity, type ActivityQueue, type ActivityValue } from "./activity";
import type { HomeDetailTarget } from "./home";

export type ReactionState = { liked?: boolean; preference?: Preference | null };
export type ReactionControls = ReturnType<typeof createReactionControls>;
export function createReactionControls(activity: ActivityQueue, api = { likeContent, unlikeContent, setPreference, clearPreference }) {
  const values = new Map<string, ReactionState>(), pending = new Map<string, Promise<unknown>>(), listeners = new Set<() => void>();
  let revision = 0, generation = 0;
  const controllers = new Set<AbortController>();
  const key = (target: HomeDetailTarget) => `${target.topicId}:${target.contentId}`;
  function emit() { revision++; listeners.forEach(listener => listener()); }
  function mutate(target: HomeDetailTarget, kind: "like" | "preference", initial: ReactionState, desired: boolean | Preference | null, surface: ActivityValue["surface"]) {
    activity.assertOwner();
    const identity = activityIdentity(target);
    if (!identity) throw new Error("콘텐츠의 관심 분야를 확인할 수 없어요.");
    const id = key(target), lock = `${id}:${kind}`;
    if (pending.has(lock)) return pending.get(lock)!;
    const epoch = generation, abort = new AbortController(); controllers.add(abort);
    const request = Promise.resolve().then(async () => {
      activity.assertOwner();
      const signed = await activity.authorize();
      if (generation !== epoch || abort.signal.aborted) throw new Error("로그인 상태가 변경되었어요.");
      const init = { ...signed, signal: abort.signal };
      if (kind === "like") {
        if (desired) {
          const result = await api.likeContent(identity.contentId, identity.topicId, init);
          if (result.contentId !== identity.contentId || result.liked !== true) throw new Error("좋아요 결과를 확인하지 못했어요.");
        } else await api.unlikeContent(identity.contentId, identity.topicId, init);
      } else if (desired === null) await api.clearPreference(identity.contentId, identity.topicId, init);
      else {
        const result = await api.setPreference(identity.contentId, identity.topicId, desired as Preference, init);
        if (result.contentId !== identity.contentId || result.topicId !== identity.topicId || result.value !== desired) throw new Error("선호 결과를 확인하지 못했어요.");
      }
      if (generation !== epoch || abort.signal.aborted) throw new Error("로그인 상태가 변경되었어요.");
      activity.assertOwner();
      values.set(id, { ...initial, ...values.get(id), [kind === "like" ? "liked" : "preference"]: desired });
      activity.record(target, kind === "like" ? desired ? "like" : "like_cancel" : desired === "positive" ? "preference_positive" : desired === "negative" ? "preference_negative" : "preference_cancel", { surface });
    }).finally(() => { controllers.delete(abort); if (epoch === generation) { pending.delete(lock); emit(); } });
    pending.set(lock, request); emit(); return request;
  }
  return {
    value(target: HomeDetailTarget, initial: ReactionState = {}): ReactionState { return { ...initial, ...values.get(key(target)) }; },
    isPending(target: HomeDetailTarget, kind: "like" | "preference") { return pending.has(`${key(target)}:${kind}`); },
    toggleLike(target: HomeDetailTarget, initial: ReactionState, surface: ActivityValue["surface"]) {
      return mutate(target, "like", initial, !(values.get(key(target))?.liked ?? initial.liked ?? false), surface);
    },
    choosePreference(target: HomeDetailTarget, initial: ReactionState, value: Preference, surface: ActivityValue["surface"]) {
      const current = values.has(key(target)) && values.get(key(target))?.preference !== undefined ? values.get(key(target))!.preference : initial.preference;
      return mutate(target, "preference", initial, current === value ? null : value, surface);
    },
    clear() { generation++; controllers.forEach(c => c.abort()); controllers.clear(); pending.clear(); values.clear(); emit(); },
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    getSnapshot() { return revision; },
  };
}
