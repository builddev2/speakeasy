import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';

export function DockApp() {
  return (
    <GlassPanel width={360} height={300}>
      <TitleBar plain />
      <div>Dock — placeholder</div>
    </GlassPanel>
  );
}
