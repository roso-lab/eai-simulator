// Gazebo world plugin: periodically prints every model's world pose while
// the world runs. Gate 4 of the usd-to-gazebo skill -- the only check that
// sees exactly what physics and the GUI see (sdformat pose parsing
// included).
//
// Gazebo inserts <include> models asynchronously while the world runs, so
// a one-shot dump at plugin-load time races model creation. This plugin
// instead prints a fresh snapshot every 100 simulation updates; the driver
// script waits until the model count is stable and complete.
//
// Build (from this directory):  bash build_native.sh
// Output lines (repeated over time; take the last snapshot):
//   PROBE_SNAPSHOT models=N
//   PROBE_POSE <name> pos=x y z rot_wxyz=w,x,y,z

#include <gazebo/common/UpdateInfo.hh>
#include <gazebo/gazebo.hh>
#include <gazebo/physics/physics.hh>
#include <gazebo/common/Event.hh>

namespace gazebo {
class WorldProbe : public WorldPlugin {
  public: void Load(physics::WorldPtr world, sdf::ElementPtr) override
  {
    this->updateConn = event::Events::ConnectWorldUpdateBegin(
        [this, world](const common::UpdateInfo &)
        {
          if (++this->ticks % 100 != 0)
            return;
          std::cerr << "PROBE_SNAPSHOT models=" << world->ModelCount() << std::endl;
          for (auto const &m : world->Models())
          {
            auto p = m->WorldPose();
            std::cerr << "PROBE_POSE " << m->GetName()
                      << " pos=" << p.Pos()
                      << " rot_wxyz=" << p.Rot().W() << "," << p.Rot().X()
                      << "," << p.Rot().Y() << "," << p.Rot().Z() << std::endl;
          }
        });
  }

  private: event::ConnectionPtr updateConn;
  private: int ticks = 0;
};
GZ_REGISTER_WORLD_PLUGIN(WorldProbe)
}
