import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';
import { AppIdentity } from '../components/AppIdentity';
import { StatusDot } from '../components/StatusDot';

export function DockApp() {
  return (
    <GlassPanel width={360} height={300}>
      <TitleBar plain />
      <AppIdentity status={<><StatusDot variant="green" /> Ready — Jason</>} />
    </GlassPanel>
  );
}
