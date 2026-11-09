"use client";

import { useEffect, useState } from "react";
import { createInquiryController, initialInquiryState, type InquirySource, type InquiryState } from "@/lib/pivot/inquiry";
import { InquiryScreen } from "./inquiry-screen";

export function InquiryController({ source, demo, onMy, onHome, onSaved }: {
  source?: InquirySource; demo: boolean; onMy: () => void; onHome: () => void; onSaved: () => void;
}) {
  const [request, setRequest] = useState<{
    source?: InquirySource; demo: boolean; state: InquiryState; control: ReturnType<typeof createInquiryController>;
  }>();
  useEffect(() => {
    const control = createInquiryController(source, demo, state => setRequest({ source, demo, control, state }));
    setRequest({ source, demo, control, state: initialInquiryState() });
    return () => control.cancel();
  }, [source, demo]);
  const current = request?.source === source && request?.demo === demo ? request : undefined;
  return <InquiryScreen state={current?.state ?? initialInquiryState()} demo={demo}
    available={Boolean(current && (demo || source))} onChange={draft => current?.control.change(draft)}
    onSubmit={() => { void current?.control.submit(); }} onMy={onMy} onHome={onHome} onSaved={onSaved} />;
}
