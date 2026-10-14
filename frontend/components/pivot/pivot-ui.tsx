import type { ButtonHTMLAttributes, ReactNode } from "react";
import styles from "./pivot.module.css";

type Screen = "splash" | "login" | "consent" | "accepted";
export function PivotAsset({ screen, name, ...props }: { screen: Screen; name: string } & React.ImgHTMLAttributes<HTMLImageElement>) {
  const extension = name === "imgImage24" || name === "imgImage26" || name === "img2" ? "png" : "svg";
  return <img src={`/pivot/${screen}-${name}.${extension}`} alt="" aria-hidden="true" {...props} />;
}
export function PivotBrand({ screen, className = "" }: { screen: Screen; className?: string }) {
  return <div className={`${styles.brand} ${className}`}><PivotAsset screen={screen} name="img4Vectorized" alt="me;nu" aria-hidden={undefined} width={140} height={48} /></div>;
}
export function PivotFrame({ children, splash = false, chrome = false, decorated = true, className = "" }: { children: ReactNode; splash?: boolean; chrome?: boolean; decorated?: boolean; className?: string }) {
  return <main className={`${styles.screen} ${splash ? styles.splash : ""} ${className}`}>
    {!splash && decorated && <div className={styles.decorations} aria-hidden="true">
      <PivotAsset screen="login" name="img" className={styles.decorationTop} width={380} height={380} />
      <PivotAsset screen="login" name="img1" className={styles.decorationSide} width={247} height={254} />
    </div>}
    {chrome && <DesignStatusBar />}
    {children}
  </main>;
}
function DesignStatusBar() {
  return <div className={styles.statusBar} aria-hidden="true">
    <span>9:41</span><div className={styles.statusIcons}>
      <PivotAsset screen="login" name="imgIconMobileSignal" width={17.308} height={11.538} />
      <PivotAsset screen="login" name="imgWifi" width={16.346} height={11.379} />
      <span className={styles.battery}><PivotAsset screen="login" name="imgOutline" width={24.038} height={12.5} /><b>70</b></span>
      <PivotAsset screen="login" name="imgBatteryEnd" width={1.347} height={4.058} className={styles.batteryEnd} />
    </div>
  </div>;
}
export function PrimaryButton({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" className={`${styles.primary} ${className}`} {...props} />;
}
