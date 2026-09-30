"use client";

import { MySubPage } from "@/components/my/my-sub-page";
import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { consumerRoutes } from "@/lib/consumer-routes";

// 크리에이터 팔로우 API(API-045~047)가 연결되면 이 목록을 채운다.
const subscriptions: Array<{ channelId: string; name: string }> = [];

export function MySubscriptionsScreen({ topicId }: { topicId: string | null }) {
  const { flowState, isRestoring } = useConsumerFlow();
  const currentTopicId = topicId ?? flowState?.initialHomeTopicId ?? null;

  if (isConsumerApiMode && isRestoring) return null;

  return (
    <MySubPage
      backHref={currentTopicId ? { pathname: consumerRoutes.my, query: { topicId: currentTopicId } } : consumerRoutes.my}
    >
      <h1 className="va-my-subscribe-title">{subscriptions.length}명 구독 중</h1>
      {subscriptions.length === 0 ? (
        <p role="status" className="va-message va-muted text-center text-sm">
          아직 구독한 크리에이터가 없어요.
        </p>
      ) : (
        <ul className="va-my-topic-list">
          {subscriptions.map((channel) => (
            <li key={channel.channelId} className="va-my-topic-row">
              <span>{channel.name}</span>
            </li>
          ))}
        </ul>
      )}
    </MySubPage>
  );
}
