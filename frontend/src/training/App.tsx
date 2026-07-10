import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';

export function TrainingApp() {
  return (
    <GlassPanel width={640} height={440}>
      <TitleBar title="Training" />
      <div>Training — placeholder</div>
    </GlassPanel>
  );
}
