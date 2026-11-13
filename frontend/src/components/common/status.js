import {JobStatus,DeliveryStatus} from '../../constants/enums.js';
import {esc} from '../../utils/dom.js';
const labels={...JobStatus,...DeliveryStatus};
export const status=v=>'<span class="status '+esc(v)+'">'+esc(labels[v]||v)+'</span>';
