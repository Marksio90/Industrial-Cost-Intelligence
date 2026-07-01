import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import { Activity, Box, DollarSign, TrendingUp } from 'lucide-react'

const fetchHealth = async () => {
  const { data } = await axios.get('/api/v1/health')
  return data
}

const StatCard = ({ title, value, icon: Icon, color }: any) => (
  <div className="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
    <div className="flex items-center justify-between">
      <div>
        <p className="text-sm text-gray-500">{title}</p>
        <p className="text-2xl font-bold mt-1">{value}</p>
      </div>
      <div className={`p-3 rounded-lg ${color}`}>
        <Icon size={20} className="text-white" />
      </div>
    </div>
  </div>
)

export default function Dashboard() {
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: fetchHealth })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard title="Status" value={health?.status ?? '—'} icon={Activity} color="bg-emerald-500" />
        <StatCard title="Materials" value="1,240" icon={Box} color="bg-blue-500" />
        <StatCard title="Cost Estimates" value="€4.2M" icon={DollarSign} color="bg-amber-500" />
        <StatCard title="Forecasts" value="89%" icon={TrendingUp} color="bg-violet-500" />
      </div>
      <div className="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <h2 className="text-lg font-semibold mb-4">Platform Overview</h2>
        <p className="text-gray-600">
          Industrial Cost Intelligence (ICI) is an enterprise-grade platform for manufacturing cost estimation,
          price forecasting, and autonomous RFQ management. The platform integrates 11 microservices with
          ML-driven cost prediction, vector similarity search, and AI-powered supplier negotiation.
        </p>
      </div>
    </div>
  )
}
