import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./pivot.module.css";

type Screen = "splash" | "login" | "consent" | "accepted";
export function PivotAsset({ screen, name, ...props }: { screen: Screen; name: string } & React.ImgHTMLAttributes<HTMLImageElement>) {
  const extension = name === "imgImage24" || name === "imgImage26" || name === "imgImage28" || name === "imgImage36" || name === "img2" ? "png" : "svg";
  return <img src={`/pivot/${screen}-${name}.${extension}`} alt="" aria-hidden="true" {...props} />;
}
export function PivotBrand({ screen, className = "" }: { screen: Screen; className?: string }) {
  return <div className={`${styles.brand} ${className}`}><PivotAsset screen={screen} name="img4Vectorized" alt="me;nu" aria-hidden={undefined} width={140} height={27.9811} /></div>;
}
export function PivotFrame({ children, splash = false, decorated = true, className = "" }: { children: ReactNode; splash?: boolean; chrome?: boolean; decorated?: boolean; className?: string }) {
  return <main className={`${styles.screen} ${splash ? styles.splash : ""} ${className}`}>
    {!splash && decorated && <div className={styles.decorations} aria-hidden="true">
      <PivotAsset screen="login" name="img" className={styles.decorationTop} width={380} height={380} />
      <PivotAsset screen="login" name="img1" className={styles.decorationSide} width={247} height={254} />
    </div>}
    {children}
  </main>;
}
export function PrimaryButton({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" className={`${styles.primary} ${className}`} {...props} />;
}
