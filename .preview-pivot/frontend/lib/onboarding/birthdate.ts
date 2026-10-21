import type { AgeEligibilityStatus } from "@/types/consumer";

type BirthdateValidationReason = "required" | "invalid" | "future";

export type BirthdateValidationResult =
  | { valid: true }
  | { valid: false; reason: BirthdateValidationReason };

type CalendarDate = {
  year: number;
  month: number;
  day: number;
};

const BIRTHDATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const MINIMUM_AGE = 14;

function isLeapYear(year: number) {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function getDaysInMonth(year: number, month: number) {
  if (month === 2) {
    return isLeapYear(year) ? 29 : 28;
  }

  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function parseBirthdate(value: string): CalendarDate | null {
  const match = BIRTHDATE_PATTERN.exec(value);

  if (!match) {
    return null;
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);

  if (
    year < 1 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > getDaysInMonth(year, month)
  ) {
    return null;
  }

  return { year, month, day };
}

function getCalendarDate(date: Date): CalendarDate {
  return {
    year: date.getFullYear(),
    month: date.getMonth() + 1,
    day: date.getDate(),
  };
}

function compareCalendarDates(left: CalendarDate, right: CalendarDate) {
  if (left.year !== right.year) {
    return left.year - right.year;
  }

  if (left.month !== right.month) {
    return left.month - right.month;
  }

  return left.day - right.day;
}

function getEligibilityDate(birthdate: CalendarDate): CalendarDate {
  const eligibilityYear = birthdate.year + MINIMUM_AGE;

  if (
    birthdate.month === 2 &&
    birthdate.day === 29 &&
    !isLeapYear(eligibilityYear)
  ) {
    return {
      year: eligibilityYear,
      month: 3,
      day: 1,
    };
  }

  return {
    year: eligibilityYear,
    month: birthdate.month,
    day: birthdate.day,
  };
}

export function formatDateInputValue(date = new Date()) {
  const { year, month, day } = getCalendarDate(date);

  return [year, month, day]
    .map((value, index) =>
      index === 0 ? String(value).padStart(4, "0") : String(value).padStart(2, "0"),
    )
    .join("-");
}

export function validateBirthdate(
  birthdate: string,
  referenceDate = new Date(),
): BirthdateValidationResult {
  if (!birthdate) {
    return { valid: false, reason: "required" };
  }

  const parsedBirthdate = parseBirthdate(birthdate);

  if (!parsedBirthdate) {
    return { valid: false, reason: "invalid" };
  }

  if (compareCalendarDates(parsedBirthdate, getCalendarDate(referenceDate)) > 0) {
    return { valid: false, reason: "future" };
  }

  return { valid: true };
}

export function getAgeEligibility(
  birthdate: string,
  referenceDate = new Date(),
): AgeEligibilityStatus {
  const validation = validateBirthdate(birthdate, referenceDate);

  if (!validation.valid) {
    throw new Error(`Invalid birthdate: ${validation.reason}`);
  }

  const parsedBirthdate = parseBirthdate(birthdate);

  if (!parsedBirthdate) {
    throw new Error("Invalid birthdate");
  }

  const eligibilityDate = getEligibilityDate(parsedBirthdate);

  return compareCalendarDates(getCalendarDate(referenceDate), eligibilityDate) >=
    0
    ? "eligible"
    : "restricted";
}
