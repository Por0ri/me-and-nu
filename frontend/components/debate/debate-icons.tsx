import type { SVGProps } from "react";

/** 토론방 화면용 선 아이콘. 색은 글자색(currentColor)을 따른다. */
type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 20, children, ...props }: IconProps) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}>{children}</svg>;
}

export const StarIcon = (props: IconProps) => <Icon {...props}><path d="m12 3.5 2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z" /></Icon>;
export const ClockIcon = (props: IconProps) => <Icon {...props}><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></Icon>;
export const UsersIcon = (props: IconProps) => <Icon {...props}><circle cx="9" cy="8.5" r="3.2" /><path d="M3.5 19c.6-3 2.8-4.8 5.5-4.8s4.9 1.8 5.5 4.8" /><path d="M15.5 5.6a3 3 0 0 1 0 5.8M17.6 14.6c1.6.6 2.6 2.1 2.9 4.4" /></Icon>;
export const InfinityIcon = (props: IconProps) => <Icon {...props}><path d="M7.5 15.5c-2 0-3.5-1.6-3.5-3.5S5.5 8.5 7.5 8.5c3.5 0 5.5 7 9 7 2 0 3.5-1.6 3.5-3.5s-1.5-3.5-3.5-3.5c-3.5 0-5.5 7-9 7z" /></Icon>;
export const ChatIcon = (props: IconProps) => <Icon {...props}><path d="M20 11.5c0 4.1-3.6 7.5-8 7.5-1.2 0-2.3-.2-3.3-.6L4 20l1.3-3.6C4.5 15 4 13.3 4 11.5 4 7.4 7.6 4 12 4s8 3.4 8 7.5z" /></Icon>;
export const ChatFilledIcon = (props: IconProps) => <Icon {...props} fill="currentColor" stroke="none"><path d="M12 3.5c4.7 0 8.5 3.6 8.5 8s-3.8 8-8.5 8c-1.3 0-2.5-.3-3.6-.7L3.6 20.4l1.4-4C4.2 15 3.5 13.3 3.5 11.5c0-4.4 3.8-8 8.5-8z" /><circle cx="8.5" cy="11.5" r="1.1" fill="#fff" /><circle cx="12" cy="11.5" r="1.1" fill="#fff" /><circle cx="15.5" cy="11.5" r="1.1" fill="#fff" /></Icon>;
export const TicketIcon = (props: IconProps) => <Icon {...props}><path d="M4 8.5 15.8 4.6l1.1 3.1a2.2 2.2 0 0 0 1.4 4.1l1.1 3.1L7.6 18.8l-1.1-3.1a2.2 2.2 0 0 0-1.4-4.1z" /><path d="m10.7 7.6 2.6 7.6" strokeDasharray="1.5 2" /></Icon>;
export const CalendarIcon = (props: IconProps) => <Icon {...props}><rect x="4" y="5.5" width="16" height="14" rx="2" /><path d="M8 3.5v4M16 3.5v4M4 10h16" /></Icon>;
export const FlameIcon = (props: IconProps) => <Icon {...props} fill="currentColor" stroke="none"><path d="M12.6 2.8c.4 3-1.4 4.4-2.8 6-1.2 1.3-2.3 2.8-2.3 5.1a4.5 4.5 0 0 0 9 0c0-1.7-.6-3-1.3-4.1-.2 1.1-.8 1.9-1.6 2.3.4-2.8-.1-6.4-1-9.3z" /></Icon>;
export const BoltIcon = (props: IconProps) => <Icon {...props}><path d="M13 3 5 13.5h6l-1 7.5 8-10.5h-6z" /></Icon>;
export const InfoIcon = (props: IconProps) => <Icon {...props}><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 7.8v.1" /></Icon>;
export const ChevronRightIcon = (props: IconProps) => <Icon {...props}><path d="m9.5 6 6 6-6 6" /></Icon>;
export const ChevronLeftIcon = (props: IconProps) => <Icon {...props}><path d="m14.5 6-6 6 6 6" /></Icon>;
export const SendIcon = (props: IconProps) => <Icon {...props}><path d="M20.5 3.5 10 14M20.5 3.5 14 20.5l-4-6.5-6.5-4z" /></Icon>;
export const SearchIcon = (props: IconProps) => <Icon {...props}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></Icon>;
export const BellIcon = (props: IconProps) => <Icon {...props}><path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.5H5z" /><path d="M10 20.5a2.2 2.2 0 0 0 4 0" /></Icon>;
export const HomeIcon = (props: IconProps) => <Icon {...props}><path d="M4.5 10.5 12 4.5l7.5 6V19a1 1 0 0 1-1 1h-4v-5h-5v5h-4a1 1 0 0 1-1-1z" /></Icon>;
export const BookmarkIcon = (props: IconProps) => <Icon {...props}><path d="M6.5 4.5h11v15.5L12 16l-5.5 4z" /></Icon>;
export const UserIcon = (props: IconProps) => <Icon {...props}><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.6 7-5.6s6.2 2 7 5.6" /></Icon>;
export const CheckIcon = (props: IconProps) => <Icon {...props}><path d="m5 12.5 4.5 4.5L19 7.5" /></Icon>;
export const PlusIcon = (props: IconProps) => <Icon {...props}><path d="M12 5v14M5 12h14" /></Icon>;
export const CloseIcon = (props: IconProps) => <Icon {...props}><path d="m6 6 12 12M18 6 6 18" /></Icon>;
export const TrophyIcon = (props: IconProps) => <Icon {...props}><path d="M8 4.5h8v5a4 4 0 0 1-8 0zM8 6.5H4.5c0 3 1.5 4.5 3.8 4.6M16 6.5h3.5c0 3-1.5 4.5-3.8 4.6M12 13.5v4M8.5 20h7" /></Icon>;
export const ListIcon = (props: IconProps) => <Icon {...props}><path d="m4 6.5 1.5 1.5L8 5.5M11 7h9M4 12.5 5.5 14 8 11.5M11 13h9M11 19h9M4.5 19h2" /></Icon>;
export const SparkleIcon = (props: IconProps) => <Icon {...props}><path d="M12 3.5c.6 4.4 2.1 5.9 6.5 6.5-4.4.6-5.9 2.1-6.5 6.5-.6-4.4-2.1-5.9-6.5-6.5 4.4-.6 5.9-2.1 6.5-6.5zM18.5 15.5c.3 1.9.9 2.5 2.8 2.8-1.9.3-2.5.9-2.8 2.8-.3-1.9-.9-2.5-2.8-2.8 1.9-.3 2.5-.9 2.8-2.8z" /></Icon>;
export const ChatPlusIcon = (props: IconProps) => <Icon {...props}><path d="M19 12.5c0 3.6-3.1 6.5-7 6.5-1 0-2-.2-2.9-.5L5 20l1.2-3.2C5.4 15.7 5 14.2 5 12.5 5 8.9 8.1 6 12 6s7 2.9 7 6.5z" /><path d="M12 9.8v5.4M9.3 12.5h5.4M17.5 3.5l1-1.5M20 6l1.5-1M19.5 3.8l1.3-.6" /></Icon>;
/** 보라 동그라미 안 흰 체크 (완료 알림, 2026-11-23 시안). 색이 정해진 아이콘이다. */
export const CheckCircleIcon = ({ size = 22 }: { size?: number }) => <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <circle cx="12" cy="12" r="11" fill="#7043b4" /><path d="m7.2 12.4 3.2 3.2 6.4-6.6" fill="none" stroke="#fff" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
</svg>;
/** 동그라미 안 체크 (채팅방 진영 고르기 버튼, 2026-11-23 시안) */
export const CircleCheckIcon = (props: IconProps) => <Icon {...props}><circle cx="12" cy="12" r="9" /><path d="m8.3 12.2 2.5 2.5 4.9-5" /></Icon>;
