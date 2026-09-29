"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import Image from "next/image";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { saveContent, unsaveContent } from "@/lib/consumer-api/contents";
import {
  readContentLikedState,
  subscribeContentState,
  toggleContentLikedState,
} from "@/lib/content-state";
import { shareContent } from "@/lib/share-content";

type ContentCardActionsProps = {
  contentId: string;
  title: string;
  topicId: string;
  saved: boolean;
};

export function ContentCardActions({
  contentId,
  title,
  topicId,
  saved,
}: ContentCardActionsProps) {
  const input = { contentId, topicContext: { topicId } };
  const liked = useSyncExternalStore(
    subscribeContentState,
    () => readContentLikedState(input),
    () => false,
  );
  const [pendingAction, setPendingAction] = useState<"save" | "share" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const actionInFlight = useRef(false);
  const isMounted = useRef(false);

  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  async function runAction(action: "save" | "share") {
    if (actionInFlight.current) {
      return;
    }

    actionInFlight.current = true;
    setPendingAction(action);
    setMessage(null);

    try {
      let nextMessage: string | null;
      if (action === "save") {
        const result = await (saved ? unsaveContent(input) : saveContent(input));
        if (
          result.contentId !== contentId ||
          result.topicContext.topicId !== topicId
        ) {
          throw new Error("Save response does not match the card context.");
        }
        nextMessage = result.saved ? "저장했습니다." : "저장을 취소했습니다.";
      } else {
        const url = new URL(
          consumerRoutes.content(contentId),
          window.location.origin,
        );
        url.searchParams.set("topicId", topicId);
        const result = await shareContent({ title, url: url.href });
        nextMessage = result === "copied"
          ? "링크를 복사했습니다."
          : result === "shared"
            ? "공유했습니다."
            : null;
      }
      if (isMounted.current) {
        setMessage(nextMessage);
      }
    } catch {
      if (isMounted.current) {
        setMessage(
          action === "save"
            ? "저장 상태를 변경하지 못했습니다. 다시 시도해 주세요."
            : "공유하지 못했습니다. 브라우저 권한을 확인하고 다시 시도해 주세요.",
        );
      }
    } finally {
      actionInFlight.current = false;
      if (isMounted.current) {
        setPendingAction(null);
      }
    }
  }

  function toggleLike() {
    setMessage(null);
    try {
      toggleContentLikedState(input);
    } catch {
      setMessage("좋아요를 변경하지 못했습니다. 다시 시도해 주세요.");
    }
  }

  return (
    <>
      <div className="va-card-actions" role="group" aria-label={`${title} 동작`}>
        <button
          type="button"
          aria-label="공유"
          disabled={pendingAction !== null}
          onClick={() => void runAction("share")}
          className="va-card-action"
        >
          <Image src="/ui-home/share.svg" alt="" width={24} height={24} />
        </button>
        <button
          type="button"
          aria-label={saved ? "저장 취소" : "저장"}
          aria-pressed={saved}
          disabled={pendingAction !== null}
          onClick={() => void runAction("save")}
          className="va-card-action"
        >
          <Image
            src={saved ? "/ui-home/bookmark-selected.svg" : "/ui-home/bookmark.svg"}
            alt=""
            width={24}
            height={24}
          />
        </button>
        <button
          type="button"
          aria-label={liked ? "좋아요 취소" : "좋아요"}
          aria-pressed={liked}
          disabled={pendingAction !== null}
          onClick={toggleLike}
          className="va-card-action"
        >
          <Image
            src={liked ? "/ui-home/like-selected.svg" : "/ui-home/like.svg"}
            alt=""
            width={24}
            height={24}
          />
        </button>
      </div>
      <p role="status" className={message ? "va-card-action-feedback" : "sr-only"}>
        {message}
      </p>
    </>
  );
}
