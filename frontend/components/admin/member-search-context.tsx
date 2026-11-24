"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

const MemberSearch = createContext<{ search: string; setSearch: (value: string) => void }>({ search: "", setSearch: () => {} });
export function MemberSearchProvider({ children }: { children: ReactNode }) {
  const [search, setSearch] = useState("");
  return <MemberSearch.Provider value={{ search, setSearch }}>{children}</MemberSearch.Provider>;
}
export function useMemberSearch() { return useContext(MemberSearch); }
