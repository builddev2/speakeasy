import { GlassPanel } from '../components/GlassPanel';
import { TitleBar } from '../components/TitleBar';

export function MeetingsApp() {
  return (
    <GlassPanel width={720} height={480}>
      <TitleBar title="Meetings" />
      <div>Meetings — placeholder</div>
    </GlassPanel>
  );
}
