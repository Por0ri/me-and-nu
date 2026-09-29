/** Consumer UI keeps its presentation fixtures unless local API mode is selected. */
export type ConsumerDataMode = "mock" | "api";

export const consumerDataMode: ConsumerDataMode =
  process.env.NEXT_PUBLIC_CONSUMER_DATA_MODE === "api" ? "api" : "mock";

export const isConsumerApiMode = consumerDataMode === "api";
