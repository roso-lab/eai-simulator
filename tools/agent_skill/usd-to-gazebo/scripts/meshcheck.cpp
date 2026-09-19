// Load DAE files through gazebo's own MeshManager and print vertex counts
// and bounds. Gate 2 of the usd-to-gazebo skill: every mesh must load and
// its bounds must match the USD side (assimp may split seam vertices, so
// compare bounds, not counts).
//
// Build (from this directory):  bash build_native.sh
// Usage: ./meshcheck file1.dae [file2.dae ...]
// Exit status is 0 only if every file loads.

#include <gazebo/common/Mesh.hh>
#include <gazebo/common/MeshManager.hh>
#include <iostream>

int main(int argc, char **argv)
{
  int bad = 0;
  for (int i = 1; i < argc; ++i)
  {
    const gazebo::common::Mesh *m = gazebo::common::MeshManager::Instance()->Load(argv[i]);
    if (!m || m->GetVertexCount() == 0)
    {
      std::cout << "FAIL " << argv[i] << "\n";
      ++bad;
      continue;
    }
    auto mn = m->Min(), mx = m->Max();
    std::cout << "OK " << argv[i] << " verts=" << m->GetVertexCount()
              << " min=(" << mn.X() << "," << mn.Y() << "," << mn.Z() << ")"
              << " max=(" << mx.X() << "," << mx.Y() << "," << mx.Z() << ")\n";
  }
  return bad == 0 ? 0 : 1;
}
