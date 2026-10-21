import type { IconType } from "react-icons";
import {
  LuArrowLeft, LuCamera, LuCheck, LuChevronDown, LuChevronLeft, LuChevronRight, LuCircleHelp, LuEye, LuFileCheck, LuFlag, LuHand,
  LuInfo, LuMaximize, LuMinus, LuPause, LuPlay, LuPlus, LuSettings, LuSkipBack, LuSkipForward, LuSquare, LuStepBack, LuStepForward,
  LuTriangleAlert, LuUndo2, LuShieldCheck, LuUsers, LuDatabase, LuCpu, LuSparkles, LuChartColumn, LuServer, LuFolder, LuImage,
  LuSearch, LuLayoutGrid, LuList, LuArrowRight, LuEllipsis, LuKeyboard, LuExternalLink, LuTrash2, LuCircleCheck, LuX, LuCircleAlert,
  LuTag, LuClipboardCheck, LuUser, LuLock, LuEyeOff, LuTerminal, LuKeyRound, LuCopy, LuPower, LuRefreshCw, LuUpload,
} from "react-icons/lu";

/** Iconos de react-icons (juego Lucide). El resto de la interfaz los pide por nombre. */
const ICONS = {
  hand: LuHand, square: LuSquare, plus: LuPlus, minus: LuMinus, fit: LuMaximize, play: LuPlay, pause: LuPause,
  skipBack: LuSkipBack, skipFwd: LuSkipForward, stepBack: LuStepBack, stepFwd: LuStepForward, camera: LuCamera, eye: LuEye,
  flag: LuFlag, check: LuCheck, back: LuUndo2, help: LuCircleHelp, down: LuChevronDown, right: LuChevronRight, left: LuChevronLeft,
  alert: LuTriangleAlert, doc: LuFileCheck, gear: LuSettings, info: LuInfo, goBack: LuArrowLeft,
  shield: LuShieldCheck, users: LuUsers, database: LuDatabase, cpu: LuCpu, sparkles: LuSparkles, chart: LuChartColumn, server: LuServer,
  folder: LuFolder, image: LuImage, search: LuSearch, grid: LuLayoutGrid, list: LuList, arrowRight: LuArrowRight, more: LuEllipsis,
  keyboard: LuKeyboard, external: LuExternalLink, trash: LuTrash2, checkCircle: LuCircleCheck, x: LuX, alertCircle: LuCircleAlert,
  tag: LuTag, clipboard: LuClipboardCheck, user: LuUser, lock: LuLock, eyeOff: LuEyeOff, terminal: LuTerminal, key: LuKeyRound, copy: LuCopy, power: LuPower, refresh: LuRefreshCw, upload: LuUpload,
} satisfies Record<string, IconType>;

export type IconName = keyof typeof ICONS;
const FILLED = new Set<IconName>(["play", "pause", "skipBack", "skipFwd", "stepBack", "stepFwd"]);

export default function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  const C = ICONS[name];
  return <C className="ico" size={size} aria-hidden fill={FILLED.has(name) ? "currentColor" : "none"} />;
}
